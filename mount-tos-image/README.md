# mount-tos-image
This script will help you to mount TOS/GEMDOS partitioned harddrives (e.g. SD-/CF-Card or images) directly under Linux.

## Preface

  * You have just upgraded your old Atari to use modern storage (SD-/CF-Card)? Great!
  * You have partitioned it on the Atari without byte swap to make it bootable? Perfect!
  * You want to directly mount it under Linux to be able to use native tools? Yes!

BAM! A lot of crap hindering you from doing that is thrown in your way:

  * The data on the medium is byte swapped (big endian) and needs to be swapped for Linux to recognize anything on it.
  * Unfortunately Linux does not have a byte-swapping device mapper.
  * Creating an image and flashing it back is not only slow, it's a complete overkill and the flash cells will scream at you!
  * Even if you manage to create an on-the-fly byte-swapped-image, Linux still refuses to mount certain partitions, because FAT has a limit of 4096 bytes per sector and Linux sticks to this rule.

With so many things in your way, you simply give up. Or do you?
No! Of course not! Seek no more, this script does a whole lot of crazy magic to overcome all these problems!

## Prerequisites

  * Python >= 3.6
  * parted
  * fallocate (util-linux)
  * [A patched version of xmount](https://github.com/Curly060/xmount)
  * Your TOS media you want to access with linux as partitioned device (as block device or image file)

## Installation

  * Copy the script to some place which is in your PATH and make sure it is executable.
  * Build and install [my version of xmount](https://github.com/Curly060/xmount)


## The Clue

Every FAT partition has a bootsector which contains important information such as:
  * BPS: bytes per sector
  * SPC: sectors per cluster
	* SPF: sectors per FAT
  * NSECTS/HSECTS: number of sectors the partition occupies (NSECTS = word, HSECTS = long)

In FAT terms a sector always means "logical" sector.

GEMDOS can only handle exactly 2 sectors per cluster and doesn't know about the HSECTS field. So in order to use bigger partitions the BPS is increased.
Unfortunately this is against the FAT specification which only allows a maximum of 4096 BPS and Linux sticks to this specification.

This is the reason why Linux refuses to mount bigger TOS formatted partitions.

But since all calculations in the FAT are done using clusters all we need to do is placing such values in the bootsector that BPS is never bigger than 4096 and that the cluster size is equal in TOS and DOS!

Of course this manipulation is not done directly on the SD-/CF-Card. Instead, the script makes heavy use of some nice and cool Linux features (FUSE and device mapper).

## How to start the script

* Connect the TOS medium of your choice as block device to the linux system or simply use an image file  
* Start the script from within a working directory on a file system that is capable of creating spares files (e.g. btrfs, ext4, ...)
* When started in "mount" mode, the script will create temporaray sparse files within the current directory 
* When started in "umount" mode, the script will also delete these temporaray sparse files

## What the script does

In short: Well, there is not short explanation ;)

In "mount" mode the script will do all of these things:
  * mount the device as virtual device with a write cache using xmount doing byte swap on the fly if necessary
  * create a sparse file of the same size as the given device
  * partition that sparse file exactly like under TOS but using Linux tools to have 100% linux compatible partitioning
  * calculate DOS/Linux compatible bootsector values from the TOS ones and write them to the sparse file
  * use dmsetup to create a device where all sectors come from the xmounted image except for the manipulated sectors which are take from the sparse image
  * use losetup -P to detect the partitions

=> Now the Linux desktop shows the Atari drives and you can mount them.

In "umount" mode the script will do these things:
  * write all blocks in the xmount cache file back to the actual device performing byte swap if necessary
  * remove all temporarily created devices and images

## How to use the script

The script has a built-in help:
```bash
usage: mount-tos-image.py [-h] {mount,umount} device deviceName

TOS Image Mount Helper

positional arguments:
  {mount,umount}  action to perform
  device          path to SD-/CF-Card device (block device or image file)
  deviceName      name of dmsetup device

optional arguments:
  -h, --help      show this help message and exit
```

### Partition the device
Simply partition the drive to your liking using an up-to-date harddisk driver on your Atari.

Stick to the following rules:

  * Do not make partitions bigger than 2GB.
  * Do not enable the byte swap in the harddisk driver.
  * If you use Hatari/Aranym to setup the device, make sure that you do enable byte swap

### Label the partitions

While not required I highly recommend that you label your partitions. The label is part of the bootsector and will be used by Linux and thus make it so much easier to recognize which partition is which.

Unfortunately the Atari desktop can't do this and neither can HDDRiver (at least not in version 9), but any other decent desktop should do the trick (tested with Jinnee).

### Mount the device

Given the script is in your PATH and the medium is for oyur Milan:
```bash
sudo mount-tos-image mount /dev/sdX milan
```

This will a new device /dev/mapper/milan as described above and print out the involved loop devices for your information.. You should now see the partitions in your favorite file explorer. You can either directly mount them there or mount them via command line.

### Do your work

Happily use all Linux power:
  * ncdu: ncurses based disk usage
  * rmlint: very good duplicate finder
  * find, grep etc.

### Unmount

Unmount all partitions that you mounted. Only then call the script again to actually write back all changes:

```bash
sudo mount-tos-image umount /dev/sdX milan
```

Note: In the current version, you cannot umount without write back
