#!/usr/bin/python
import os
import sys
import subprocess
import struct
import re
from dataclasses import dataclass, astuple
import argparse
from pathlib import Path
import json
import shutil

# Parse Arguments
parser = argparse.ArgumentParser(description='TOS Image Mount Helper')
parser.add_argument("action", choices = ['mount', 'umount'], help="action to perform")
parser.add_argument("device", help="path to SD-/CF-Card device")
parser.add_argument("deviceName", help="name of dmsetup device")
args = parser.parse_args()

partedEnv = os.environ.copy()
partedEnv['LC_ALL'] = 'C'
class Device:
    def __init__(self, device, ignoreErrors = False):
        # WTF: parted's machine parseable output doesn't show the partitino types, so we need to parse text :(
        result = subprocess.run(['parted', '-s', device, 'unit', 'b', 'print'], env = partedEnv, capture_output = True, text = True)
        resultStderr = result.stderr.splitlines()
        if len(resultStderr):
            self.isByteSwapped = True
            if not ignoreErrors:
                for err in resultStderr:
                    print(err, file = sys.stderr)
                raise ValueError
            return
        resultStdout = result.stdout.splitlines()
        tableDefinitionStart = [resultStdout.index(x) for x in resultStdout if x.startswith('Number')][0] + 1
        self.diskInfo = dict(re.split(": +", x) for x in resultStdout if ': ' in x)
        self.size = [int(v.strip('B')) for k,v in self.diskInfo.items() if k.startswith('Disk') and v.endswith('B')][0]
        self.partitions = [Partition(device, line) for line in resultStdout[tableDefinitionStart:] if line]
        self.path = device

class Partition:
    def __init__(self, device, partedLine):
        self.num, self.start, self.end, self.size = map(lambda x: int(x.strip('B')), partedLine.split()[0:4])
        self.partType = partedLine.split()[4]
        self.bootsector = Bootsector.fromDevice(device, self.start) if self.partType in ('logical', 'primary') else None

    def getInfo(self):
        return (self.num, self.start, self.end, self.size, self.partType, self.bootsector)

    def isFAT16Partition(self):
        return self.bootsector.isFAT16() if self.bootsector else False

@dataclass
class Bootsector:
    STRUCT = '<3s8sHBHBHHBHHHII3sI11s8s448sH'

    BRA: bytes
    OEM: bytes
    BPS: int
    SPC: int
    RES: int
    NFATS: int
    NDIRS: int
    NSECTS: int
    MEDIA: int
    SPF: int
    SPT: int
    NHEADS: int
    NHID: int
    HSECTS: int
    DUMMY: bytes
    ID: int
    NAME: bytes
    VARIANT: bytes
    REST: bytes
    SIGNATURE: int

    def fromDevice(device, offset):
        with open(device, 'rb') as f:
            f.seek(offset,0)
            return Bootsector(*struct.unpack(Bootsector.STRUCT, f.read(512)))

    def __generateDOSBootsector(self):
        dosBootsector = Bootsector(*astuple(self))
        dosBootsector.BPS = 4096
        factor = self.BPS // dosBootsector.BPS
        dosBootsector.SPC = self.SPC * factor
        dosBootsector.RES = self.RES * factor
        dosBootsector.NSECTS = 0
        dosBootsector.HSECTS = self.NSECTS * factor
        dosBootsector.SPF = self.SPF * factor
        dosBootsector.SIGNATURE = 0xaa55
        return dosBootsector

    def dosBootsectorToBytes(self):
        dosFields = self.__generateDOSBootsector()
        return struct.pack(Bootsector.STRUCT, *astuple(dosFields));

    def isFAT16(self):
        return self.VARIANT in (b'\xff\xff?<\x00HNA', b'FAT16   ')

@dataclass
class CacheFileBlockIndex:
    STRUCT = '<LQ'
    index: int
    Assigned: int
    off_data: int

@dataclass
class CacheFileHeader:
    STRUCT = '<8sLQQQLQLQQLQ432s'
    FileSignature: int
    CacheFileVersion: int
    BlockSize: int
    BlockCount: int
    pBlockIndex: int
    VdiFileHeaderCached: int
    pVdiFileHeader: int
    VmdkFileCached: int
    VmdkFileSize: int
    pVmdkFile: int
    VhdFileHeaderCached: int
    pVhdFileHeader: int
    HeaderPadding: bytes

def findNonzeroSectors(file):
    output = subprocess.run(['cmp', '-l', file, '/dev/zero'], capture_output = True, text = True).stdout.splitlines()
    offsets = set((int(x.split()[0]) - 1)//512 for x in output)
    return sorted(offsets)

def generatePartedCommands(device):
    partedCommands = ['mktable msdos', 'unit b']
    for p in device.partitions:
        num, start, end, size, partType, _ = p.getInfo() 
        if partType == 'extended':
            partedCommands.append('mkpart extended {} {}'.format(start, end))
        else:
            partedCommands.append('mkpart {} {} {} {}'.format(partType, 'fat16', start, end))
    return partedCommands

def setupLoopDevice(filename, scanPartitions = False):
    command = ['losetup', '--find', '--show']
    if scanPartitions:
        command.append('-P')
    command.append(filename)
    return subprocess.run(command, capture_output = True, text = True, check = True).stdout.splitlines()[0]

def detachLoopDevice(loopDevice):
    subprocess.run(['losetup', '-d', loopDevice], text = True, check = True)
    subprocess.run(['sync'], text = True, check = True)

def removeDMDevice(dmDevice):
    subprocess.run(['dmsetup', 'remove', dmDevice], check = True, text = True)

def generateSparseFile(device, sparseFile):
    subprocess.run(['fallocate', '-l', str(device.size), sparseFile], check = True, text = True)
    subprocess.run(['parted', '-s', '-a', 'none', '-m', sparseFile] + generatePartedCommands(device), env = partedEnv, capture_output = True, text = True)
    with open(sparseFile, 'r+b') as f:
        for p in [p for p in device.partitions if p.isFAT16Partition()]:
            start = p.start
            f.seek(start)
            # TODO: use mkfs.fat here?
            f.write(p.bootsector.dosBootsectorToBytes())

def generateDmsetupLine(start, sects, device, tsects):
    return '{} {} linear {} {}'.format(start, sects, device, tsects)

def generateDmsetupTable(device, sparseFile, tosLoopDevice, dosLoopDevice):
    sectorList = findNonzeroSectors(sparseFile) + [device.size//512]
    for start, end in zip(sectorList, sectorList[1:]):
        size = end - start
        yield generateDmsetupLine(start, 1, dosLoopDevice, start)
        if size > 1:
            yield generateDmsetupLine(start + 1, size - 1, tosLoopDevice, start + 1)

def setupDevice(device, sparseFile, dmDevice):
    generateSparseFile(device, sparseFile)
    tosLoopDevice = setupLoopDevice(device.path)
    dosLoopDevice = setupLoopDevice(sparseFile)
    tables = '\n'.join(generateDmsetupTable(device, sparseFile, tosLoopDevice, dosLoopDevice))
    subprocess.run(['dmsetup', 'create', os.path.basename(dmDevice)], input = tables, check = True, text = True)
    finalDevice = setupLoopDevice(dmDevice, scanPartitions = True)
    return {'tosLoopDevice': tosLoopDevice, 'dosLoopDevice': dosLoopDevice, 'finalDevice': finalDevice}

def flushXmountCache(device, cacheFile):
    with open(cacheFile, 'rb') as f:
        cfh = CacheFileHeader(*struct.unpack(CacheFileHeader.STRUCT, f.read(512)))
        blockIndex = f.read(cfh.BlockCount*(4 + 8))
        for x in [x for x in map(lambda x: CacheFileBlockIndex(*(x[0], *x[1])), enumerate(struct.iter_unpack(CacheFileBlockIndex.STRUCT, blockIndex))) if x.Assigned == 1]:
            subprocess.run(['dd', 'if=' + cacheFile, 'of=' + device, 'bs=' + str(cfh.BlockSize), 'count=1', 'skip=' + str(x.off_data), 'seek=' + str(x.index), 'iflag=skip_bytes', 'conv=swab,notrunc'], capture_output = True, check = True, text = True)

def setupDOSDevice(device, infoFile, xmountBase, xmountDataDir, sparseFile, dmDevice):
    Path(xmountDataDir).mkdir(parents = True, exist_ok = True)
    xmountCommand = ['xmount', '--in', 'raw', device, '--out', 'raw',  '--cache', os.path.join(xmountBase, 'cache')]
    if Device(device, ignoreErrors = True).isByteSwapped:
        xmountCommand += ['--morph', 'swab']
    xmountCommand.append(xmountDataDir)
    subprocess.run(xmountCommand, check = True, text = True)
    xmountImage = os.path.join(xmountDataDir, os.path.splitext(os.path.basename(device))[0]+'.dd')
    info = setupDevice(Device(xmountImage), sparseFile, dmDevice)
    print(info)
    with open(infoFile, 'w') as f:
        json.dump(info, f)

def removeDOSDevice(device, infoFile, xmountBase, xmountDataDir, sparseFile, dmDevice):
    with open(infoFile, 'r') as f:
        info = json.load(f)
    detachLoopDevice(info['finalDevice'])
    removeDMDevice(dmDevice)
    for loopDevice in [v for k,v in info.items() if k != 'finalDevice']:
        detachLoopDevice(loopDevice)
    subprocess.run(['umount', xmountDataDir], check = True, text = True)
    flushXmountCache(device, os.path.join(xmountBase, 'cache'))
    shutil.rmtree(xmountBase)
    os.unlink(sparseFile)
    os.unlink(infoFile)

infoFile = os.path.basename(args.device) + '.info.json'
dmDevice = '/dev/mapper/' + args.deviceName
xmountBase = '/tmp/xmount/{}'.format(args.deviceName)
xmountDataDir = os.path.join(xmountBase, 'data')
sparseFile = os.path.basename(args.device) + '.sparse'

if args.action == 'mount':
    if Path(dmDevice).is_block_device():
        print('Can''t mount. Block device {} already exists.'.format(dmDevice), file = sys.stderr)
        sys.exit(1)
    setupDOSDevice(args.device, infoFile, xmountBase, xmountDataDir, sparseFile, dmDevice)
if args.action == 'umount':
    if Path(dmDevice).is_block_device():
        removeDOSDevice(args.device, infoFile, xmountBase, xmountDataDir, sparseFile, dmDevice)
    else:
        print('Device {} not present. Nothing to do.'.format(dmDevice))

