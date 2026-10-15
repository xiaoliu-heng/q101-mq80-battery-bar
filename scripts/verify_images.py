"""Check the two immutable release images offline. Never opens a device."""
import hashlib
import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "stock.bin": (126292, 0xB00C451E, "d19733978808c856db93236ea3472b2cf843493c9aa6d5320db50412431e5210"),
    "battery-bar-v2.bin": (126756, 0x068F4D5A, "f3e86554be1ed5faf11750fceea85980c9ccd080912be4552e8317fb09ef7719"),
}


def main():
    results = []
    for name, (size, crc, digest) in EXPECTED.items():
        data = (ROOT / "simulator" / "assets" / name).read_bytes()
        checks = [len(data) == size, data[0x20:0x24] == b"KNLT",
                  struct.unpack_from("<I", data, 0x18)[0] == size,
                  struct.unpack("<I", data[-4:])[0] == crc,
                  (zlib.crc32(data[:-4]) ^ 0xffffffff) == crc,
                  (len(data) - 4) % 16 == 0,
                  hashlib.sha256(data).hexdigest() == digest]
        if not all(checks):
            raise SystemExit("Image verification failed: " + name)
        results.append({"file": name, "size": size, "crc": "%08X" % crc, "sha256": digest})
    print(json.dumps({"status": "PASS", "images": results, "device_access": False}, indent=2))


if __name__ == "__main__":
    main()
