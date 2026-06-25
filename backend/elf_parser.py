"""Aurora ELF Binary Parser — parse ELF headers, sections, symbols.

Mirrors the Worker's ELF parsing capability from Codex reverse engineering.
Supports 32-bit and 64-bit ELF files (Linux executables, shared objects, core dumps).
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, BinaryIO


class ELFClass(IntEnum):
    CLASS32 = 1
    CLASS64 = 2

class ELFEndian(IntEnum):
    LITTLE = 1
    BIG = 2

class ELFType(IntEnum):
    NONE = 0
    REL = 1
    EXEC = 2
    DYN = 3
    CORE = 4

ELF_TYPE_NAMES = {0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}

class ELFMachine(IntEnum):
    X86 = 3
    X86_64 = 62
    ARM = 40
    AARCH64 = 183
    RISCV = 243

ELF_MACHINE_NAMES = {3: "x86", 62: "x86_64", 40: "ARM", 183: "AArch64", 243: "RISC-V"}

SHT_NAMES = {
    0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA",
    5: "HASH", 6: "DYNAMIC", 7: "NOTE", 8: "NOBITS", 9: "REL",
    10: "SHLIB", 11: "DYNSYM", 14: "INIT_ARRAY", 15: "FINI_ARRAY",
    16: "PREINIT_ARRAY", 17: "GROUP", 18: "SYMTAB_SHNDX",
}

PT_NAMES = {
    0: "NULL", 1: "LOAD", 2: "DYNAMIC", 3: "INTERP", 4: "NOTE",
    5: "SHLIB", 6: "PHDR", 7: "TLS", 0x6474E550: "GNU_EH_FRAME",
    0x6474E551: "GNU_STACK", 0x6474E552: "GNU_RELRO",
    0x6474E553: "GNU_PROPERTY",
}


@dataclass
class ELFHeader:
    magic: bytes = b""
    elf_class: int = 0
    endian: int = 0
    version: int = 0
    os_abi: int = 0
    abi_version: int = 0
    elf_type: int = 0
    machine: int = 0
    entry_point: int = 0
    phoff: int = 0      # program header offset
    shoff: int = 0      # section header offset
    flags: int = 0
    ehsize: int = 0     # ELF header size
    phentsize: int = 0  # program header entry size
    phnum: int = 0      # program header count
    shentsize: int = 0  # section header entry size
    shnum: int = 0      # section header count
    shstrndx: int = 0   # section name string table index

    def to_dict(self) -> dict:
        return {
            "type": ELF_TYPE_NAMES.get(self.elf_type, str(self.elf_type)),
            "machine": ELF_MACHINE_NAMES.get(self.machine, str(self.machine)),
            "class": "ELF64" if self.elf_class == 2 else "ELF32",
            "endian": "little" if self.endian == 1 else "big",
            "entry_point": hex(self.entry_point),
            "program_headers": self.phnum,
            "section_headers": self.shnum,
            "abi": f"0x{self.os_abi:02x}",
        }


@dataclass
class ELFSection:
    name: str = ""
    index: int = 0
    sh_type: int = 0
    flags: int = 0
    addr: int = 0
    offset: int = 0
    size: int = 0
    link: int = 0
    info: int = 0
    addralign: int = 0
    entsize: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": SHT_NAMES.get(self.sh_type, f"0x{self.sh_type:x}"),
            "addr": hex(self.addr),
            "offset": hex(self.offset),
            "size": self.size,
        }


@dataclass
class ELFProgramHeader:
    p_type: int = 0
    offset: int = 0
    vaddr: int = 0
    paddr: int = 0
    filesz: int = 0
    memsz: int = 0
    flags: int = 0
    align: int = 0

    def to_dict(self) -> dict:
        perm = ""
        perm += "R" if self.flags & 4 else "-"
        perm += "W" if self.flags & 2 else "-"
        perm += "X" if self.flags & 1 else "-"
        return {
            "type": PT_NAMES.get(self.p_type, f"0x{self.p_type:x}"),
            "vaddr": hex(self.vaddr),
            "filesz": hex(self.filesz),
            "memsz": hex(self.memsz),
            "flags": perm,
        }


@dataclass
class ELFInfo:
    header: ELFHeader = field(default_factory=ELFHeader)
    sections: list[ELFSection] = field(default_factory=list)
    program_headers: list[ELFProgramHeader] = field(default_factory=list)
    file_path: str = ""
    file_size: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.file_path,
            "size": self.file_size,
            "header": self.header.to_dict(),
            "program_headers": [p.to_dict() for p in self.program_headers],
            "sections": [s.to_dict() for s in self.sections if s.name],
        }


class ELFParser:
    """Parse ELF binaries (Linux executables, .so, .o files)."""

    ELF_MAGIC = b"\x7fELF"

    @classmethod
    def is_elf(cls, path: str | Path) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(4) == cls.ELF_MAGIC
        except (OSError, PermissionError):
            return False

    @classmethod
    def parse(cls, path: str | Path) -> ELFInfo:
        path = Path(path)
        info = ELFInfo(file_path=str(path), file_size=path.stat().st_size)
        with open(path, "rb") as f:
            info.header = cls._parse_header(f)
            f.seek(info.header.phoff)
            for i in range(info.header.phnum):
                ph = cls._parse_program_header(f, info.header.elf_class, info.header.endian)
                info.program_headers.append(ph)
            f.seek(info.header.shoff)
            for i in range(info.header.shnum):
                sh = cls._parse_section_header(f, info.header.elf_class, info.header.endian)
                sh.index = i
                info.sections.append(sh)
            # Resolve section names from string table
            if 0 < info.header.shstrndx < len(info.sections):
                shstr = info.sections[info.header.shstrndx]
                for sec in info.sections:
                    if sec.name == "" and shstr.size > 0:
                        f.seek(shstr.offset + sec.info)
                        raw = b""
                        while True:
                            b = f.read(1)
                            if b == b"\x00" or not b:
                                break
                            raw += b
                        try:
                            sec.name = raw.decode("ascii")
                        except UnicodeDecodeError:
                            sec.name = raw.decode("utf-8", errors="replace")
        return info

    @classmethod
    def _parse_header(cls, f: BinaryIO) -> ELFHeader:
        h = ELFHeader()
        magic = f.read(4)
        if magic != cls.ELF_MAGIC:
            raise ValueError(f"Not an ELF file: magic={magic!r}")
        h.magic = magic
        h.elf_class = struct.unpack("B", f.read(1))[0]
        h.endian = struct.unpack("B", f.read(1))[0]
        end = "<" if h.endian == 1 else ">"
        h.version = struct.unpack("B", f.read(1))[0]
        h.os_abi = struct.unpack("B", f.read(1))[0]
        h.abi_version = struct.unpack("B", f.read(1))[0]
        f.read(7)  # padding
        h.elf_type = struct.unpack(end + "H", f.read(2))[0]
        h.machine = struct.unpack(end + "H", f.read(2))[0]
        h.version = struct.unpack(end + "I", f.read(4))[0]
        if h.elf_class == 2:
            h.entry_point = struct.unpack(end + "Q", f.read(8))[0]
            h.phoff = struct.unpack(end + "Q", f.read(8))[0]
            h.shoff = struct.unpack(end + "Q", f.read(8))[0]
        else:
            h.entry_point = struct.unpack(end + "I", f.read(4))[0]
            h.phoff = struct.unpack(end + "I", f.read(4))[0]
            h.shoff = struct.unpack(end + "I", f.read(4))[0]
        h.flags = struct.unpack(end + "I", f.read(4))[0]
        h.ehsize = struct.unpack(end + "H", f.read(2))[0]
        h.phentsize = struct.unpack(end + "H", f.read(2))[0]
        h.phnum = struct.unpack(end + "H", f.read(2))[0]
        h.shentsize = struct.unpack(end + "H", f.read(2))[0]
        h.shnum = struct.unpack(end + "H", f.read(2))[0]
        h.shstrndx = struct.unpack(end + "H", f.read(2))[0]
        return h

    @classmethod
    def _parse_section_header(cls, f: BinaryIO, elf_class: int, endian: int) -> ELFSection:
        end = "<" if endian == 1 else ">"
        sh = ELFSection()
        sh.info = struct.unpack(end + "I", f.read(4))[0]  # name offset in shstrtab
        sh.sh_type = struct.unpack(end + "I", f.read(4))[0]
        if elf_class == 2:
            sh.flags = struct.unpack(end + "Q", f.read(8))[0]
            sh.addr = struct.unpack(end + "Q", f.read(8))[0]
            sh.offset = struct.unpack(end + "Q", f.read(8))[0]
            sh.size = struct.unpack(end + "Q", f.read(8))[0]
        else:
            sh.flags = struct.unpack(end + "I", f.read(4))[0]
            sh.addr = struct.unpack(end + "I", f.read(4))[0]
            sh.offset = struct.unpack(end + "I", f.read(4))[0]
            sh.size = struct.unpack(end + "I", f.read(4))[0]
        sh.link = struct.unpack(end + "I", f.read(4))[0]
        sh.info = sh.info
        if elf_class == 2:
            sh.addralign = struct.unpack(end + "Q", f.read(8))[0]
            sh.entsize = struct.unpack(end + "Q", f.read(8))[0]
        else:
            sh.addralign = struct.unpack(end + "I", f.read(4))[0]
            sh.entsize = struct.unpack(end + "I", f.read(4))[0]
        return sh

    @classmethod
    def _parse_program_header(cls, f: BinaryIO, elf_class: int, endian: int) -> ELFProgramHeader:
        end = "<" if endian == 1 else ">"
        ph = ELFProgramHeader()
        ph.p_type = struct.unpack(end + "I", f.read(4))[0]
        if elf_class == 2:
            ph.flags = struct.unpack(end + "I", f.read(4))[0]
            ph.offset = struct.unpack(end + "Q", f.read(8))[0]
            ph.vaddr = struct.unpack(end + "Q", f.read(8))[0]
            ph.paddr = struct.unpack(end + "Q", f.read(8))[0]
            ph.filesz = struct.unpack(end + "Q", f.read(8))[0]
            ph.memsz = struct.unpack(end + "Q", f.read(8))[0]
            ph.align = struct.unpack(end + "Q", f.read(8))[0]
        else:
            ph.offset = struct.unpack(end + "I", f.read(4))[0]
            ph.vaddr = struct.unpack(end + "I", f.read(4))[0]
            ph.paddr = struct.unpack(end + "I", f.read(4))[0]
            ph.filesz = struct.unpack(end + "I", f.read(4))[0]
            ph.memsz = struct.unpack(end + "I", f.read(4))[0]
            ph.flags = struct.unpack(end + "I", f.read(4))[0]
            ph.align = struct.unpack(end + "I", f.read(4))[0]
        return ph


# Singleton
elf_parser = ELFParser()
