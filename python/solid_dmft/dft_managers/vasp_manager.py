#!/usr/bin/env python3
# -*- coding: utf-8 -*-
################################################################################
#
# solid_dmft - A versatile python wrapper to perform DFT+DMFT calculations
#              utilizing the TRIQS software library
#
# Copyright (C) 2018-2020, ETH Zurich
# Copyright (C) 2021, The Simons Foundation
#      authors: A. Hampel, M. Merkel, and S. Beck
#
# solid_dmft is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# solid_dmft is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along with
# solid_dmft (in the file COPYING.txt in this directory). If not, see
# <http://www.gnu.org/licenses/>.
#
################################################################################

"""
Contains the handling of the VASP process. It can start VASP, reactivate it,
check if the lock file is there and finally kill VASP. Needed for CSC calculations.
"""

import os
import signal

import triqs.utility.mpi as mpi

from solid_dmft.dft_managers import mpi_helpers


def _fork_and_start_vasp(mpi_exe, arguments, env_vars):
    """
    Forks a process from the master process that then calls mpi to start vasp.
    The child process running VASP never leaves this function whereas the main
    process returns the child's process id and continues. We use explicitly
    os.fork as os.execve here because subprocess does not return, and as VASP
    needs to keep running throughout the whole DFT+DMFT calculation that is
    not a viable solution here.

    Parameters
    ----------
    mpi_exe: string, mpi command
    arguments: list of string, arguments to start mpi with
    env_vars: dict of string, environment variables containing PATH

    Returns
    -------
    int: id of the VASP child process
    """

    # fork process
    vasp_process_id = os.fork()
    if vasp_process_id == 0:
        # close file descriptors, if rank0 had open files
        for fd in range(3, 256):
            try:
                os.close(fd)
            except OSError:
                pass
        print('\n Starting VASP now\n')
        os.execve(mpi_exe, arguments, env_vars)
        print('\n VASP exec failed\n')
        os._exit(127)

    return vasp_process_id


def start(number_cores, vasp_command, cluster_name):
    """
    Starts the VASP child process. Takes care of initializing a clean
    environment for the child process. This is needed so that VASP does not
    get confused with all the standard slurm environment variables.

    Parameters
    ----------
    number_cores: int, the number of cores that vasp runs on
    vasp_command: string, the command to start vasp
    cluster_name: string, name of the cluster so that settings can be tailored to it
    """

    # Removes STOPCAR
    if mpi.is_master_node() and os.path.isfile('STOPCAR'):
        os.remove('STOPCAR')

    # get MPI env
    vasp_process_id = 0

    hostfile = mpi_helpers.create_hostfile(number_cores, cluster_name)

    if mpi.is_master_node():
        # clean environment
        env_vars = {}
        for var_name in ['PATH', 'LD_LIBRARY_PATH', 'SHELL', 'PWD', 'HOME',
                         'OMP_NUM_THREADS', 'OMPI_MCA_btl_vader_single_copy_mechanism']:
            var = os.getenv(var_name)
            if var:
                env_vars[var_name] = var

        # assuming that mpirun points to the correct mpi env
        mpi_exe = mpi_helpers.find_path_to_mpi_command(env_vars, 'mpirun')

        arguments = mpi_helpers.get_mpi_arguments(cluster_name, mpi_exe, number_cores, vasp_command, hostfile)
        vasp_process_id = _fork_and_start_vasp(mpi_exe, arguments, env_vars)

    mpi_helpers.poll_barrier(mpi.MPI.COMM_WORLD)
    vasp_process_id = mpi.bcast(vasp_process_id)

    return vasp_process_id


def reactivate():
    """ Reactivates VASP by creating the vasp.lock file. """
    if mpi.is_master_node():
        open('./vasp.lock', 'a').close()
    mpi.barrier()


def is_lock_file_present():
    """
    Checks if the lock file 'vasp.lock' is there, i.e. if VASP is still working.
    """

    res_bool = False
    if mpi.is_master_node():
        res_bool = os.path.isfile('./vasp.lock')
    mpi.barrier()
    res_bool = mpi.bcast(res_bool)
    return res_bool


def kill(vasp_process_id):
    """ Kills the VASP process. """
    # TODO: if we kill the process in the next step, does it make a difference if we write the STOPCAR
    with open('STOPCAR', 'wt') as f_stop:
        f_stop.write('LABORT = .TRUE.\n')
    os.kill(vasp_process_id, signal.SIGTERM)
    mpi.MPI.COMM_WORLD.Abort(1)