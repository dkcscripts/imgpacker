#!/usr/bin/env python3
"""
imgpacker — embed encrypted archives inside PNG images

Usage:
  imgpacker.py pack -o <output.png> <file/dir> [file/dir ...]
  imgpacker.py extract <image.png>

Combines packer.sh concepts (tar + xz + gpg + base64) with PNG LSB steganography.
"""

import argparse
import base64
import io
import os
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from getpass import getpass

try:
    from PIL import Image
except ImportError:
    print(
        "[imgpacker] ERROR: Pillow not installed.\n"
        "Install with: pip install Pillow",
        file=sys.stderr,
    )
    sys.exit(1)

# ── constants ────────────────────────────────────────────────────────────────

PAYLOAD_MARKER = b"__IMGPACKER_PAYLOAD__"
MIN_CARRIER_SIZE = (512, 512)  # minimal PNG carrier if none provided
BITS_PER_PIXEL = 2  # embed 2 bits per pixel (LSB of R and G channels)

# ── helpers ──────────────────────────────────────────────────────────────────


def info(msg):
    print(f"[imgpacker] {msg}", file=sys.stderr)


def error(msg):
    print(f"[imgpacker] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"[imgpacker] WARNING: {msg}", file=sys.stderr)


# ── tar + compress + encrypt ────────────────────────────────────────────────

def list_files_to_add(inputs: list) -> int:
    """
    Recursively list all files that will be added to archive.
    Returns total file count.
    """
    total_files = 0
    for inp in inputs:
        if os.path.isfile(inp):
            info(f"  → {inp}")
            total_files += 1
        elif os.path.isdir(inp):
            for root, dirs, files in os.walk(inp):
                for file in files:
                    filepath = os.path.join(root, file)
                    info(f"  → {filepath}")
                    total_files += 1
    return total_files


def walk_files(path: str, prefix: str = "") -> list:
    """
    Recursively walk directory and return list of (filepath, display_name) tuples.
    """
    files = []
    try:
        for entry in os.listdir(path):
            full_path = os.path.join(path, entry)
            display = prefix + entry
            if os.path.isfile(full_path):
                files.append((full_path, display))
            elif os.path.isdir(full_path):
                files.extend(walk_files(full_path, display + "/"))
    except PermissionError:
        pass
    return files


def create_encrypted_payload(inputs: list) -> bytes:
    """
    Create tar archive, compress with xz, encrypt with gpg.
    Returns encrypted binary payload (will be embedded in image).
    """
    info(f"Inputs: {', '.join(inputs)}")
    info("Files to add:")
    total_files = list_files_to_add(inputs)
    info(f"Total: {total_files} files")
    info("Stages: tar | xz | gpg --symmetric | base64")

    # Validate inputs exist
    for inp in inputs:
        if not os.path.exists(inp):
            error(f"Input not found: {inp}")

    # Create tar in memory
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:") as tar:
        for inp in inputs:
            tar.add(inp, arcname=os.path.basename(inp))
    tar_bytes.seek(0)
    tar_data = tar_bytes.read()
    tar_bytes.seek(0)
    
    info(f"Archive size: {len(tar_data)} bytes (uncompressed)")

    # Compress with xz (need subprocess, no native xz module)
    try:
        xz_proc = subprocess.run(
            ["xz", "-zv", "-"],
            input=tar_data,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        error("'xz' not found on PATH")
    except subprocess.CalledProcessError as e:
        error(f"xz compression failed: {e.stderr.decode()}")

    xz_data = xz_proc.stdout

    # Encrypt with GPG (symmetric)
    try:
        # Use --batch to avoid TTY issues in some environments
        gpg_proc = subprocess.run(
            [
                "gpg",
                "--symmetric",
                "--cipher-algo",
                "AES256",
                "--batch",
                "--no-tty",
                "-",
            ],
            input=xz_data,
            capture_output=True,
        )
        if gpg_proc.returncode != 0:
            error(f"GPG encryption failed: {gpg_proc.stderr.decode()}")
    except FileNotFoundError:
        error("'gpg' not found on PATH")

    encrypted_data = gpg_proc.stdout

    # Encode to base64 for safe embedding
    payload = base64.b64encode(encrypted_data)

    info(f"Compressed size: {len(xz_data)} bytes (after xz)")
    info(f"Compression ratio: {100*len(xz_data)//len(tar_data)}% ({len(xz_data)}/{len(tar_data)})")
    info(f"Encrypted + encoded payload: {len(payload)} bytes (base64)")
    return payload


def decrypt_payload(payload: bytes, password: str) -> bytes:
    """
    Decrypt and decompress payload.
    Inverse of create_encrypted_payload.
    """
    # Decode from base64
    encrypted_data = base64.b64decode(payload)

    # Decrypt with GPG
    try:
        gpg_proc = subprocess.run(
            ["gpg", "--decrypt", "--quiet", "--batch", "--no-tty", "-"],
            input=encrypted_data,
            capture_output=True,
            text=False,
        )
        if gpg_proc.returncode != 0:
            error(f"GPG decryption failed: {gpg_proc.stderr.decode()}")
    except FileNotFoundError:
        error("'gpg' not found on PATH")

    xz_data = gpg_proc.stdout

    # Decompress with xz
    try:
        xz_proc = subprocess.run(
            ["xz", "-dv", "-"],
            input=xz_data,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        error("'xz' not found on PATH")
    except subprocess.CalledProcessError as e:
        error(f"xz decompression failed: {e.stderr.decode()}")

    tar_data = xz_proc.stdout

    return tar_data


# ── LSB steganography ────────────────────────────────────────────────────────


def calculate_minimum_image_size(payload_bytes: int) -> tuple:
    """
    Calculate minimum image dimensions needed for payload.
    Returns (width, height) tuple.
    """
    # Add overhead for marker + size
    total_bytes = payload_bytes + len(PAYLOAD_MARKER) + 4
    # Calculate pixels needed (2 bits per pixel)
    pixels_needed = (total_bytes * 8 + BITS_PER_PIXEL - 1) // BITS_PER_PIXEL
    # Square-ish dimensions
    size = int(pixels_needed**0.5) + 1
    return (size, size)


def encode_lsb(data: bytes, image: Image.Image) -> Image.Image:
    """
    Embed data into PNG using LSB (least significant bits).
    Encodes 2 bits per pixel (into R and G channels, leaving B/A untouched).
    Returns modified PIL Image.
    """
    # Validate capacity
    pixels = image.width * image.height
    max_bits = pixels * BITS_PER_PIXEL
    max_bytes = max_bits // 8
    needed_bytes = len(data) + len(PAYLOAD_MARKER) + 4  # marker + size header

    if needed_bytes * 8 > max_bits:
        # Calculate minimum image size needed
        min_pixels = (needed_bytes * 8 + BITS_PER_PIXEL - 1) // BITS_PER_PIXEL
        min_size = int(min_pixels**0.5) + 1
        error(
            f"Payload too large for image: {needed_bytes} bytes needed, "
            f"but only {max_bytes} bytes capacity in {image.width}×{image.height} ({pixels} pixels).\n"
            f"Hint: Use an image at least {min_size}×{min_size} pixels (e.g., {min_size}×{min_size} PNG) "
            f"or larger to hold {needed_bytes} bytes of data."
        )

    # Prepare data: marker + length + payload
    size_bytes = struct.pack(">I", len(data))
    full_data = PAYLOAD_MARKER + size_bytes + data

    # Convert to bits
    bits = []
    for byte in full_data:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)

    # Pad to even number of bits (we embed 2 bits per pixel)
    while len(bits) % BITS_PER_PIXEL != 0:
        bits.append(0)

    # Convert image to RGBA if needed
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    pixels_arr = image.load()
    bit_idx = 0

    for y in range(image.height):
        for x in range(image.width):
            if bit_idx >= len(bits):
                break

            r, g, b, a = pixels_arr[x, y]

            # Embed 2 bits: one in R LSB, one in G LSB
            r = (r & 0xFE) | bits[bit_idx]
            bit_idx += 1

            if bit_idx < len(bits):
                g = (g & 0xFE) | bits[bit_idx]
                bit_idx += 1
            else:
                g = g & 0xFE

            pixels_arr[x, y] = (r, g, b, a)

        if bit_idx >= len(bits):
            break

    info(f"Embedded {len(full_data)} bytes ({len(bits)} bits) in {pixels} pixels")
    return image


def decode_lsb(image: Image.Image) -> bytes:
    """
    Extract data from PNG LSB.
    Inverse of encode_lsb.
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    pixels_arr = image.load()
    bits = []

    # Extract bits from R and G channels
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels_arr[x, y]
            bits.append(r & 1)
            bits.append(g & 1)

    # Convert bits to bytes
    data = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        data.append(byte)

    data = bytes(data)

    # Verify marker
    if not data.startswith(PAYLOAD_MARKER):
        error("Payload marker not found in image")

    # Extract size
    data = data[len(PAYLOAD_MARKER) :]
    if len(data) < 4:
        error("Corrupted payload: too short")

    size = struct.unpack(">I", data[:4])[0]
    payload = data[4 : 4 + size]

    if len(payload) != size:
        error(f"Corrupted payload: expected {size} bytes, got {len(payload)}")

    info(f"Extracted {len(payload)} bytes from image")
    return payload


# ── image carrier ───────────────────────────────────────────────────────────


def create_minimal_carrier(payload_size: int) -> Image.Image:
    """
    Generate minimal PNG carrier image just large enough for payload.
    """
    # Payload size in bytes → bits needed → pixels needed (2 bits/pixel)
    bits_needed = (payload_size + len(PAYLOAD_MARKER) + 4) * 8
    bits_needed += bits_needed % BITS_PER_PIXEL  # padding
    pixels_needed = (bits_needed + BITS_PER_PIXEL - 1) // BITS_PER_PIXEL

    # Calculate width/height (square-ish)
    size = int(pixels_needed**0.5) + 1
    size = max(size, MIN_CARRIER_SIZE[0])

    info(f"Generating minimal carrier: {size}x{size} PNG")
    # Create white/light gray image
    img = Image.new("RGBA", (size, size), (200, 200, 200, 255))
    return img


def load_or_create_carrier(image_path: str = None, payload_size: int = 0) -> Image.Image:
    """
    Load image from path, or generate minimal carrier if not provided.
    """
    if image_path:
        info(f"Loading carrier image: {image_path}")
        try:
            img = Image.open(image_path)
            return img
        except Exception as e:
            error(f"Failed to load image: {e}")
    else:
        return create_minimal_carrier(payload_size)


# ── pack command ────────────────────────────────────────────────────────────


def cmd_pack(args):
    """Pack files into encrypted image."""
    if not args.inputs:
        error("At least one input file/directory required")

    if not args.output:
        error("Output image file required (-o)")

    if os.path.exists(args.output):
        response = input(
            f"[imgpacker] Output file exists: {args.output}. Overwrite? (y/N) "
        )
        if response.lower() != "y":
            info("Cancelled.")
            sys.exit(0)

    # Prompt for password
    print("[imgpacker] GPG will prompt for passphrase...", file=sys.stderr)
    print("(Same passphrase will be needed to extract)", file=sys.stderr)

    # Create payload
    payload = create_encrypted_payload(args.inputs)

    # Load or create carrier
    carrier = load_or_create_carrier(args.image, payload_size=len(payload))

    # Embed payload
    carrier = encode_lsb(payload, carrier)

    # Save image
    output_dir = os.path.dirname(args.output) or "."
    if not os.path.isdir(output_dir):
        error(f"Output directory does not exist: {output_dir}")

    try:
        carrier.save(args.output, "PNG")
        info(f"Done. {args.output} created")
    except Exception as e:
        error(f"Failed to save image: {e}")


# ── extract command ──────────────────────────────────────────────────────────


def cmd_extract(args):
    """Extract files from encrypted image."""
    image_path = args.image

    if not os.path.exists(image_path):
        error(f"Image not found: {image_path}")

    info(f"Loading image: {image_path}")
    try:
        image = Image.open(image_path)
    except Exception as e:
        error(f"Failed to load image: {e}")

    # Extract payload
    payload = decode_lsb(image)

    # Decrypt and decompress
    info("Decrypting payload...")
    tar_data = decrypt_payload(payload, None)  # password handled by gpg interactive

    # Extract tar
    info("Extracting files...")
    try:
        tar_stream = io.BytesIO(tar_data)
        with tarfile.open(fileobj=tar_stream, mode="r:") as tar:
            members = tar.getmembers()
            for member in members:
                info(f"  ← {member.name}")
            tar.extractall(".")
        info(f"Extracted {len(members)} items")
    except Exception as e:
        error(f"Failed to extract tar: {e}")

    print("", file=sys.stderr)
    info("Done. Extracted to current directory.")


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Embed encrypted archives inside PNG images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Pack directory with auto-generated carrier
  imgpacker.py pack -o backup.png ./my-app/

  # Pack with custom carrier image
  imgpacker.py pack -o backup.png -i carrier.png ./my-app/

  # Extract from image
  imgpacker.py extract backup.png
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Pack subcommand
    pack_parser = subparsers.add_parser("pack", help="Pack files into image")
    pack_parser.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="Output PNG file"
    )
    pack_parser.add_argument(
        "-i",
        "--image",
        metavar="FILE",
        help="Carrier image (auto-generated if not provided)",
    )
    pack_parser.add_argument(
        "inputs",
        nargs="+",
        metavar="INPUT",
        help="Files or directories to pack",
    )
    pack_parser.set_defaults(func=cmd_pack)

    # Extract subcommand
    extract_parser = subparsers.add_parser("extract", help="Extract files from image")
    extract_parser.add_argument("image", metavar="IMAGE", help="PNG image file")
    extract_parser.set_defaults(func=cmd_extract)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
