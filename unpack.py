from pathlib import Path
import struct

from PIL import Image

# Actually a variant of LZ77; LZSS is a very loose term
def LZSSDecode(in_, uncompressed_size):
    in_pos = 0
    out_pos = 0
    out = bytearray(uncompressed_size)

    while True:
        bit_flags = in_[in_pos] + in_[in_pos + 1] * 256
        in_pos += 2
        have_bits = 16

        # print(f"Fetch CW {bx:016b}")

        while have_bits:
            if (bit_flags & 0x8000) == 0:
                # DoRun
                code = in_[in_pos] + in_[in_pos + 1] * 256

                if code == 0:
                    # print("End via code")
                    assert out_pos == len(out)
                    return out
                else:
                    in_pos += 2

                    length = 3 + (code >> 10)
                    offset = 1 + (code & 0x3ff)

                    # print(f"CODE {code:04x} at {len(out)} copy {length} from {len(out) - offset} ({out[-offset:-offset+length]})")

                    pos = out_pos - offset

                    whole_copies = length // offset

                    if whole_copies > 0:
                        count = whole_copies * offset
                        out[out_pos:out_pos + count] = out[pos:pos + offset] * whole_copies
                        out_pos += count
                        pos += count
                        length -= count

                    # assert pos + length <= out_pos

                    out[out_pos:out_pos + length] = out[pos:pos + length]
                    out_pos += length

                bit_flags <<= 1
                have_bits -= 1
            else:
                # count number of set bits (from MSB)
                s = bin(bit_flags & 0xffff)[2:]
                span_length = s.find("0")

                if span_length < 0:
                    span_length = have_bits

                # span_length = 1
                # print("SPAN", span_length, s)
                # assert in_pos + span_length <= len(in_)
                out[out_pos:out_pos + span_length] = in_[in_pos:in_pos + span_length]
                in_pos += span_length
                out_pos += span_length

                bit_flags <<= span_length
                have_bits -= span_length


def load_palette(palette_path):
    blob = palette_path.read_bytes()
    assert len(blob) == 776

    return struct.unpack("<768B", blob[8:])


def extract_RES(path_in, dir_out, palette):
    with open(path_in, "rb") as f:
        # load header
        """
        // Header for a resource file.
        typedef struct strResfileHeader {
           ULONG    versionResFile;            // resource file version
           ULONG    oDirectory;                // offset to directory from beginning of file
           ULONG    cResources;                // total resources in file
        } RESFILE_HEADER;
        """
        versionResFile, oDirectory, cResources = struct.unpack("<III", f.read(12))
        # print(f"{oDirectory:d} COUNT={cResources:d}")
        RESUTIL_VERSION = 0x00000400
        assert versionResFile == RESUTIL_VERSION

        # load directory
        dir = []
        f.seek(oDirectory)
        for i in range(cResources):
            """
            #define cMAX_RESNAME			13

            // Directory consists of an array of DIRENTRY structures.
            typedef struct strDirEntry {
                ULONG		hashValue;						// hash value of file name
                ULONG		resOffset;						// offset to resource from beginning of file
                UBYTE		fileExtension;					// index to file extension type
                char		szName[cMAX_RESNAME];		// 8.3 filename (no path)
            } DIRENTRY, *DIRENTRY_PTR;
            """
            hashValue, resOffset, fileExtension, szName = struct.unpack("<IIB13s", f.read(22))
            # print(hashValue, resOffset, fileExtension, szName.decode())
            dir.append((hashValue, resOffset, fileExtension, szName))

        dir_out.mkdir(exist_ok=True, parents=True)

        for hashValue, resOffset, fileExtension, szName in dir:
            """
            // Header for an individual resource.
            typedef struct sResourceHeader {
                ULONG		startcode;						// RSRC string for validity check
                ULONG		cbChunk;							// total size of this chunk
                ULONG		cbCompressedData;				// size of compressed data
                ULONG		cbUncompressedData;			// size of uncompressed data
                ULONG		hashValue;						// hash value of file name
                UBYTE		flags;							// [d4-03-97 JPC] new field
                UBYTE		compressionCode;				// 0 = none, 1 = RLE, 2 = LZSS
                                                                // (note: RLE is not currently supported)
                UBYTE		fileExtension;					// index to file extension type
                char		szName[cMAX_RESNAME];		// 8.3 filename (no path)
            } RESOURCE_HEADER;
            """

            f.seek(resOffset)
            startcode, cbChunk, cbCompressedData, cbUncompressedData, hashValue, flags, compressionCode, fileExtension, szName = struct.unpack("<4sIIIIBBB13s", f.read(36))
            szName_str = szName.rstrip(b"\x00").decode()
            # print(startcode, cbChunk, cbCompressedData, cbUncompressedData, hashValue, flags, compressionCode, fileExtension, szName_str)

            print(f"{path_in.name + ':' + szName_str:30} {cbChunk=:7d} {cbCompressedData=:7d} {cbUncompressedData=:7d} {hashValue=:7d} {flags=} {compressionCode=} {fileExtension=}")

            if compressionCode == 0:    # No compression
                assert cbCompressedData == cbUncompressedData
                data = f.read(cbUncompressedData)
            elif compressionCode == 2:    # LZSS
                # print("decompress", szName)
                compressed = f.read(cbCompressedData)
                data = LZSSDecode(compressed, cbUncompressedData)
            else:
                raise ValueError(compressionCode)

            assert len(data) == cbUncompressedData

            RFF_PCX_UNCOMP = 1
            RFF_ROTATED	= 2

            if szName_str.lower().endswith(".pcx") and (flags & RFF_PCX_UNCOMP):
                # RFF_PCX_UNCOMP: it's the NOVA bitmap format, not actually PCX

                path_out = (dir_out / szName_str).with_suffix(".png")

                # data = rle_decompress(data)

                TYPEBITM = 0x000b
                w, h, scale, xcenter, type = struct.unpack("<HHHHH", data[0:10])
                assert scale == 5
                assert xcenter == 0
                assert type == TYPEBITM

                # TODO: use appropriate palette
                image = Image.frombuffer(mode="P", size=(w, h), data=data[10:])
                image.putpalette(palette, "RGB")
                if (flags & RFF_ROTATED):
                    image = image.transpose(Image.TRANSPOSE)
                image.save(path_out)
            else:
                path_out = dir_out / szName_str

                dir_out.mkdir(exist_ok=True, parents=True)
                with open(path_out, "wb") as f_out:
                    f_out.write(data)

            # print(path_out)

if __name__ == "__main__":
    self_path = Path(__file__).parent
    birthrt_path = self_path.parent

    palette_path = birthrt_path / "GRAPHICS" / "DEFAULT.COL"
    palette = load_palette(palette_path)

    #  / "BIRTHRT" / "RESFILES"
    for res_path in (self_path / "inputs").iterdir():
        print(res_path, "=>", self_path / res_path.name)
        extract_RES(res_path, self_path / res_path.name, palette)
