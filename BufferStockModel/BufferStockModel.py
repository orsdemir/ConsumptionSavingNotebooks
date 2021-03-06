# -*- coding: utf-8 -*-
"""BufferStockModel

Solves the Deaton-Carroll buffer-stock consumption model with either:

A. vfi: standard value function iteration
B. nvfi: nested value function iteration
C. egm: endogenous grid point method (egm_cpp is in C++)

"""

##############
# 1. imports #
##############

import time
import numpy as np
from numba import boolean, int32, double

# consav package
from consav.misc import elapsed, nonlinspace, create_shocks
from consav import ModelClass # baseline model class

# local modules
import utility
import last_period
import post_decision
import vfi
import nvfi
import egm
import simulate
import figs

############
# 2. model #
############

class BufferStockModelClass(ModelClass):
    
    #########
    # setup #
    #########
    
    def setup(self):
        """ set baseline parameters """   

        par = self.par

        # a. define list of non-float scalars (required!) 
        self.not_float_list = ['T','Npsi','Nxi','Nm','Np','Na','do_print','do_simple_w','simT','simN','sim_seed','cppthreads','Nshocks']
        
        # b. horizon
        par.T = 5
        
        # c. preferences
        par.beta = 0.96
        par.rho = 2.0 # if par.rho = 2 the type is incorrectly inferred as int (error rasied)

        # d. returns and income
        par.R = 1.03
        par.sigma_psi = 0.1
        par.Npsi = 6
        par.sigma_xi = 0.1
        par.Nxi = 6
        par.pi = 0.1
        par.mu = 0.5
        
        # e. grids (number of points)
        par.Nm = 600
        par.Np = 400
        par.Na = 800

        # f. misc
        par.tol = 1e-8
        par.do_print = True
        par.do_simple_w = False
        par.cppthreads = 1

        # g. simulation
        par.simT = par.T
        par.simN = 1000
        par.sim_seed = 1998
        
    def allocate(self):
        """ allocate model, i.e. create grids and allocate solution and simluation arrays """

        self.create_grids()
        self.solve_prep()
        self.simulate_prep()

    def create_grids(self):
        """ construct grids for states and shocks """

        par = self.par

        # a. states (unequally spaced vectors of length Nm)
        par.grid_m = nonlinspace(1e-6,20,par.Nm,1.1)
        par.grid_p = nonlinspace(1e-4,10,par.Np,1.1)
        
        # b. post-decision states (unequally spaced vector of length Na)
        par.grid_a = nonlinspace(1e-6,20,par.Na,1.1)
        
        # c. shocks (qudrature nodes and weights using GaussHermite)
        shocks = create_shocks(
            par.sigma_psi,par.Npsi,par.sigma_xi,par.Nxi,
            par.pi,par.mu)
        par.psi,par.psi_w,par.xi,par.xi_w,par.Nshocks = shocks

        # d. set seed
        np.random.seed(self.par.sim_seed)

    def checksum(self):
        """ print checksum """

        print(f'checksum: {np.mean(self.sol.c[0])}')

    #########
    # solve #
    #########

    def solve_prep(self):
        """ allocate memory for solution """

        par = self.par
        sol = self.sol

        sol.c = np.nan*np.ones((par.T,par.Np,par.Nm))        
        sol.v = np.nan*np.zeros((par.T,par.Np,par.Nm))
        sol.w = np.nan*np.zeros((par.Np,par.Na))
        sol.q = np.nan*np.zeros((par.Np,par.Na))

    def solve(self):
        """ solve the model using solmethod """

        par = self.par
        sol = self.sol

        # backwards induction
        for t in reversed(range(par.T)):
            
            t0 = time.time()
            
            # a. last period
            if t == par.T-1:
                
                last_period.solve(t,sol,par)

            # b. all other periods
            else:
                
                # i. compute post-decision functions
                t0_w = time.time()

                compute_w,compute_q = False,False
                if self.solmethod in ['nvfi']:
                    compute_w=True
                elif self.solmethod in ['egm']:
                    compute_q=True
                if compute_w or compute_q:
                    if self.par.do_simple_w:
                        post_decision.compute_wq_simple(t,sol,par,compute_w=compute_w,compute_q=compute_q)
                    else:
                        post_decision.compute_wq(t,sol,par,compute_w=compute_w,compute_q=compute_q)

                t1_w = time.time()

                # ii. solve bellman equation
                if self.solmethod == 'vfi':
                    vfi.solve_bellman(t,sol,par)                    
                elif self.solmethod == 'nvfi':
                    nvfi.solve_bellman(t,sol,par)
                elif self.solmethod == 'egm':
                    egm.solve_bellman(t,sol,par)                    
                else:
                    raise ValueError(f'unknown solution method, {self.solmethod}')

            # c. print
            if self.par.do_print:
                msg = f' t = {t} solved in {elapsed(t0)}'
                if t < self.par.T-1:
                    msg += f' (w: {elapsed(t0_w,t1_w)})'                
                print(msg)

    def solve_cpp(self,compiler='vs'):
        """ solve the model using egm written in C++
        
        Args:

            compiler (str,optional): compiler choice (vs or intel)

        """

        EGM = 'EGM'
        
        # a. compile
        funcnames = ['solve','simulate']
        self.setup_cpp()
        self.link_cpp(EGM,funcnames)

        # b. solve by EGM
        t0 = time.time()
       
        if self.solmethod in ['egm']:
            self.call_cpp(EGM,'solve')
        else:
            raise ValueError(f'unknown cpp solution method, {self.solmethod}')            
        
        t1 = time.time()

        # c. delink
        self.delink_cpp(EGM)

        return t0,t1

    ############
    # simulate #
    ############

    def simulate_prep(self):
        """ allocate memory for simulation """

        par = self.par
        sim = self.sim

        # a. allocate
        sim.p = np.nan*np.zeros((par.simT,par.simN))
        sim.m = np.nan*np.zeros((par.simT,par.simN))
        sim.c = np.nan*np.zeros((par.simT,par.simN))
        sim.a = np.nan*np.zeros((par.simT,par.simN))

        # b. draw random shocks
        sim.psi = np.ones((par.simT,par.simN))
        sim.xi = np.ones((par.simT,par.simN))

    def simulate(self):
        """ simulate model """

        par = self.par
        sol = self.sol
        sim = self.sim

        t0 = time.time()

        # a. allocate memory and draw random numbers
        I = np.random.choice(par.Nshocks,
            size=(par.T,par.simN), 
            p=par.psi_w*par.xi_w)
        sim.psi[:] = par.psi[I]
        sim.xi[:] = par.xi[I]

        # b. simulate
        par.simT = par.T
        simulate.lifecycle(sim,sol,par)

        if par.do_print:
            print(f'model simulated in {elapsed(t0)}')

    ########
    # figs #
    ########

    def consumption_function(self,t=0):
        figs.consumption_function(self,t)

    def consumption_function_interact(self):
        figs.consumption_function_interact(self)
          
    def lifecycle(self):
        figs.lifecycle(self)
    
    ########
    # figs #
    ########

    def test(self):
        """ method for specifying test """
        
        # a. save print status
        do_print = self.par.do_print
        self.par.do_print = False

        # b. test run
        self.solve()

        # c. timed run
        t0 = time.time()
        self.solve()
        print(f'solution time: {elapsed(t0)}')
        self.checksum()

        # d. reset print status
        self.par.do_print = do_print