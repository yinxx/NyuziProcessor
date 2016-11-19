#!/usr/bin/env python
#
# Copyright 2011-2015 Jeff Bush
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# Validates remote GDB debugger interface in emulator
#

import sys
import subprocess
import re
import os
import socket
import time

sys.path.insert(0, '..')
from test_harness import *


# Setting this to true will print all GDB commands and responses
# to the console and will also print all output from the emulator.
DEBUG = False


class DebugConnection:

    def __enter__(self):
        for retry in range(10):
            try:
                time.sleep(0.3)
                self.sock = socket.socket()
                self.sock.connect(('localhost', 8000))
                self.sock.settimeout(5)
                break
            except Exception, e:
                pass

        return self

    def __exit__(self, type, value, traceback):
        self.sock.close()

    def sendPacket(self, body):
        global DEBUG

        if DEBUG:
            print('SEND: ' + body)

        self.sock.send('$')
        self.sock.send(body)
        self.sock.send('#')

        # Checksum
        self.sock.send('\x00')
        self.sock.send('\x00')

    def receivePacket(self):
        global DEBUG

        while True:
            leader = self.sock.recv(1)
            if leader == '':
                raise TestException('unexpected socket close')

            if leader == '$':
                break

            if leader != '+':
                raise TestException('unexpected character ' + leader)

        body = ''
        while True:
            c = self.sock.recv(1)
            if c == '#':
                break

            body += c

        # Checksum
        self.sock.recv(2)

        if DEBUG:
            print('RECV: ' + body)

        return body

    def expect(self, value):
        response = self.receivePacket()
        if response != value:
            raise TestException(
                'unexpected response. Wanted ' + value + ' got ' + response)


class EmulatorTarget:

    def __init__(self, hexfile, num_cores=1):
        self.hexfile = hexfile
        self.num_cores = num_cores

    def __enter__(self):
        global DEBUG

        emulator_args = [
            BIN_DIR + 'emulator',
            '-m',
            'gdb',
            '-p',
            str(self.num_cores),
            self.hexfile
        ]

        if DEBUG:
            self.output = None
        else:
            self.output = open(os.devnull, 'w')

        self.process = subprocess.Popen(emulator_args, stdout=self.output,
                                        stderr=subprocess.STDOUT)
        return self

    def __exit__(self, type, value, traceback):
        self.process.kill()
        if self.output:
            self.output.close()


def test_breakpoint(name):
    """
    Validate stopping at a breakpoint and continuing after stopping.
    This sets two breakpoints
    """

    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Set breakpoint
        d.sendPacket('Z0,0000000c')
        d.expect('OK')

        # Set second breakpoint at next instruction
        d.sendPacket('Z0,00000010')
        d.expect('OK')

        # Continue
        d.sendPacket('C')
        d.expect('S05')

        # Read last signal
        d.sendPacket('?')
        d.expect('S05')

        # Read PC register. Should be 0x000000c, but endian swapped
        d.sendPacket('g1f')
        d.expect('0c000000')

        # Read s0, which should be 3
        d.sendPacket('g00')
        d.expect('03000000')

        # Continue again.
        d.sendPacket('C')
        d.expect('S05')

        # Ensure the instruction it stopped at is
        # executed and it breaks on the next instruction
        d.sendPacket('g1f')
        d.expect('10000000')

        # Read s0, which should be 4
        d.sendPacket('g00')
        d.expect('04000000')


def test_remove_breakpoint(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Set breakpoint
        d.sendPacket('Z0,0000000c')
        d.expect('OK')

        # Set second breakpoint
        d.sendPacket('Z0,00000014')
        d.expect('OK')

        # Clear first breakpoint
        d.sendPacket('z0,0000000c')
        d.expect('OK')

        # Continue
        d.sendPacket('C')
        d.expect('S05')

        # Read PC register. Should be at second breakpoint
        d.sendPacket('g1f')
        d.expect('14000000')

        # Read s0, which should be 5
        d.sendPacket('g00')
        d.expect('05000000')


def test_breakpoint_errors(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Set invalid breakpoint (memory out of range)
        d.sendPacket('Z0,20000000')
        d.expect('')

        # Set invalid breakpoint (unaligned)
        d.sendPacket('Z0,00000003')
        d.expect('')

        # Set a valid breakpoint, then try to set the same address again
        d.sendPacket('Z0,00000008')
        d.expect('OK')
        d.sendPacket('Z0,00000008')
        d.expect('')

        # Remove invalid breakpoint (doesn't exist)
        d.sendPacket('z0,00000004')
        d.expect('')


def test_single_step(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Read PC register
        d.sendPacket('g1f')
        d.expect('00000000')

        # Single step
        d.sendPacket('S')
        d.expect('S05')

        # Read PC register
        d.sendPacket('g1f')
        d.expect('04000000')

        # Read s0
        d.sendPacket('g00')
        d.expect('01000000')

        # Single step (note here I use the lowercase version)
        d.sendPacket('s')
        d.expect('S05')

        # Read PC register
        d.sendPacket('g1f')
        d.expect('08000000')

        # Read s0
        d.sendPacket('g00')
        d.expect('02000000')


def test_single_step_breakpoint(name):
    """
    Ensure that if you single step through a breakpoint, it doesn't
    trigger and get stuck
    """
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Set breakpoint at second instruction (address 0x8)
        d.sendPacket('Z0,00000004')
        d.expect('OK')

        # Single step over first instruction
        d.sendPacket('S')
        d.expect('S05')

        # Single step. This one has a breakpoint, but we won't
        # stop at it.
        d.sendPacket('S')
        d.expect('S05')

        # Read PC register
        d.sendPacket('g1f')
        d.expect('08000000')

        # Read s0
        d.sendPacket('g00')
        d.expect('02000000')


def test_read_write_memory(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Read program code at address 0. This should match values
        # in count.hex
        d.sendPacket('m0,10')
        d.expect('0004800700088007000c800700108007')

        # (address, data)
        tests = [
            (0x1000, '523c66b3'),
            (0x1234, '22'),
            (0x2242, '45f280397a5a3255fa19238693ff13c729'),
            (0x100000, '55483c091aac1e8c6db4bed1'),
            (0x200000, '16e1d56029e912a04121ce41a635155f3442355533703fafcb57f8295dd6330f82f9ffc40edb589fac1523665dc2f6e80c1e2de9718d253fcbce1c8a52c9dc21'),
        ]

        # Write memory
        for addr, data in tests:
            d.sendPacket('M' + hex(addr)[2:] + ',' + hex(len(data) / 2)[2:] + ':' + data)
            d.expect('OK')

        # Read and verify
        for addr, data in tests:
            d.sendPacket('m' + hex(addr)[2:] + ',' + hex(len(data) / 2)[2:])
            d.expect(data)

        # Try to write a bad address (out of range)
        # Doesn't return an error, test just ensures it
        # doesn't crash
        d.sendPacket('M10000000,4,12345678')
        d.expect('OK')

        # Try to read a bad address (out of range)
        # As above, doesn't return error (returns 0xff...),
        # but ensure it doesn't crash.
        d.sendPacket('m10000000,4')
        d.expect('ffffffff')


def test_read_write_register(name):
    hexfile = build_program(['register_values.S'])
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Run code to load registers
        d.sendPacket('C')
        d.expect('S05')

        # Check values set by program (remote GDB returns in swapped byte
        # order...)
        d.sendPacket('g1')
        d.expect('7d7f3e85')
        d.sendPacket('g20')
        d.expect('f13403ef9d08309993f7819954ae4b3f7aeaa28f538fecbd9536f59c6d7251269525ee70d26e8d34f48912639c86ae5dba426c83aa8455e1e2dbba4b41a4f321')

        tests = [
            (0, 'd3839b18'),
            (1, '7b53cc78'),
            (30, '0904c47d'),
            (32, 'aef331bc7dbd6f1d042be4d6f1e1649855d864387eb8f0fd49c205c37790d1874078516c1a05c74f67678456679ba7e05bb5aed7303c5aeeeba6e619accf702a'),
            (36, 'cb7e3668a97ef8ea55902658b62a682406f7206f75e5438ff95b4519fed1e73e16ce5a29b4385fa2560820f0c8f42227709387dbad3a8208b57c381e268ffe38'),
            (63, '9e2d89afb0633c2f64b2eb4fdbba4663401ee673753a66d6d899e4a4101ae4920b0b16f0e716e4f7d62d83b5784740c138ac6ab94fa14256ebb468e25f20e02f')
        ]

        for reg, value in tests:
            d.sendPacket('G' + hex(reg)[2:] + ',' + value)
            d.expect('OK')

        for reg, value in tests:
            d.sendPacket('g' + hex(reg)[2:])
            d.expect(value)

        # Read invalid register index
        d.sendPacket('g40')
        d.expect('')

        # Write invalid register index
        d.sendPacket('G40,12345678')
        d.expect('')


def test_register_info(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        for x in range(27):
            regid = str(x + 1)
            d.sendPacket('qRegisterInfo' + hex(x + 1)[2:])
            d.expect('name:s' + regid + ';bitsize:32;encoding:uint;format:hex;set:General Purpose Scalar Registers;gcc:'
                     + regid + ';dwarf:' + regid + ';')

        # XXX skipped fp, sp, ra, pc, which (correctly) have additional
        # info at the end.

        for x in range(32, 63):
            regid = str(x + 1)
            d.sendPacket('qRegisterInfo' + hex(x + 1)[2:])
            d.expect('name:v' + str(x - 31) + ';bitsize:512;encoding:uint;format:vector-uint32;set:General Purpose Vector Registers;gcc:'
                     + regid + ';dwarf:' + regid + ';')

        d.sendPacket('qRegisterInfo64')
        d.expect('')


def test_select_thread(name):
    hexfile = build_program(['multithreaded.S'], no_header=True)
    with EmulatorTarget(hexfile, num_cores=2) as p, DebugConnection() as d:
        # Read thread ID
        d.sendPacket('qC')
        d.expect('QC01')

        # Each line is one thread
        tests = [
            (7, 0xc7733c56),
            (5, 0xf54adec3),
            (1, 0x5afaf01e),
            (2, 0x1964682e),
            (3, 0x16cc6be1),
            (8, 0xcbff923),
            (4, 0x4596de2),
            (6, 0xcd920ca6),
        ]

        # Step all threads through initialization code (5 instructions)
        for thid in range(len(tests)):
            # Switch to thread
            d.sendPacket('Hg' + str(thid + 1))
            d.expect('OK')

            # Read thread ID
            d.sendPacket('qC')
            d.expect('QC0' + str(thid + 1))

            for index in range(5):
                d.sendPacket('S')
                d.expect('S05')

                # Read PC register
                d.sendPacket('g1f')
                d.expect('%08x' % endian_swap((index + 1) * 4))

        # Now all threads are at the same instruction:
        # 00000014 move s0, 1

        # Step each thread independently some number of steps and
        # write a value to register 1
        for index, (num_steps, regval) in enumerate(tests):
            d.sendPacket('Hg' + str(index + 1)) # Switch to thread
            d.expect('OK')
            for i in range(num_steps):
                d.sendPacket('S')
                d.expect('S05')

            d.sendPacket('G01,%08x' % regval)
            d.expect('OK')

        # Read back PC and register values
        for index, (num_steps, regval) in enumerate(tests):
            d.sendPacket('Hg' + str(index + 1)) # Switch to thread
            d.expect('OK')

            d.sendPacket('g1f')
            d.expect('%08x' % endian_swap(0x14 + num_steps * 4))
            d.sendPacket('g01')
            d.expect('%08x' % regval)

        # Try to switch to an invalid thread ID
        d.sendPacket('Hgfe')
        d.expect('')

        # Ensure still on thread 8
        d.sendPacket('qC')
        d.expect('QC08')


def test_thread_info(name):
    # Run with one core, four threads
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        d.sendPacket('qfThreadInfo')
        d.expect('m1,2,3,4')

    # Run with two cores, eight threads
    with EmulatorTarget(hexfile, num_cores=2) as p, DebugConnection() as d:
        d.sendPacket('qfThreadInfo')
        d.expect('m1,2,3,4,5,6,7,8')


def test_invalid_command(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # As far as I know, this is not a valid command...
        d.sendPacket('@')

        # An error response returns nothing in the body
        d.expect('')


def test_queries(name):
    """Miscellaneous query commands not covered in other tests"""

    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        d.sendPacket('qLaunchSuccess')
        d.expect('OK')

        d.sendPacket('qHostInfo')
        d.expect('triple:nyuzi;endian:little;ptrsize:4')

        d.sendPacket('qProcessInfo')
        d.expect('pid:1')

        d.sendPacket('qsThreadInfo')
        d.expect('l')   # No active threads

        d.sendPacket('qThreadStopInfo')
        d.expect('S00')

        d.sendPacket('qC')
        d.expect('QC01')

        # Should be invalid
        d.sendPacket('qZ')
        d.expect('')


def test_vcont(name):
    hexfile = build_program(['count.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        # Set breakpoint
        d.sendPacket('Z0,00000010')
        d.expect('OK')

        # Step
        d.sendPacket('vCont;s:0001')
        d.expect('S05')
        d.sendPacket('g1f')
        d.expect('04000000')

        # Continue
        d.sendPacket('vCont;c')
        d.expect('S05')
        d.sendPacket('g1f')
        d.expect('10000000')


def test_crash(name):
    hexfile = build_program(['crash.S'], no_header=True)
    with EmulatorTarget(hexfile) as p, DebugConnection() as d:
        d.sendPacket('c')
        d.expect('S05')
        d.sendPacket('g1f')
        d.expect('15000000')

register_tests(test_breakpoint, ['gdb_breakpoint'])
register_tests(test_remove_breakpoint, ['gdb_remove_breakpoint'])
register_tests(test_breakpoint_errors, ['gdb_breakpoint_errors'])
register_tests(test_single_step, ['gdb_single_step'])
register_tests(test_single_step_breakpoint, ['gdb_single_step_breakpoint'])
register_tests(test_read_write_memory, ['gdb_read_write_memory'])
register_tests(test_read_write_register, ['gdb_read_write_register'])
register_tests(test_register_info, ['gdb_register_info'])
register_tests(test_select_thread, ['gdb_select_thread'])
register_tests(test_thread_info, ['gdb_thread_info'])
register_tests(test_invalid_command, ['gdb_invalid_command'])
register_tests(test_queries, ['gdb_queries'])
register_tests(test_vcont, ['gdb_vcont'])
register_tests(test_crash, ['gdb_crash'])
execute_tests()
