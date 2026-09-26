# imgpacker.py

Embed **encrypted archives inside PNG images** using LSB steganography. Single Python script packs, encrypts, and hides data in pixels-no trace in image metadata.

Run on target → extract image → auto-decrypts and unpacks. Combines tar + xz + GPG AES-256 encryption with **PNG LSB steganography** (2 bits per pixel).

Perfect for covert file transfer: the image looks innocent, contains encrypted payload, requires password to extract.

---

## Requirements

| Tool/Library | Purpose                          |
|--------------|----------------------------------|
| `python3`    | 3.7+                             |
| `Pillow`     | PNG image manipulation (PIL)     |
| `xz`         | Compress/decompress              |
| `gpg`        | Symmetric encryption             |

### Install

```bash
pip install -r requirements.txt
# or manually:
pip install Pillow
```

Ensure xz and gpg on PATH:
```bash
# Linux: apt install xz-utils gnupg
# macOS: brew install xz gnupg
# Windows: use WSL or native installers from xz.tukaani.org and gnupg.org
```

---

## Usage

### Pack files into image

```bash
./imgpacker.py pack -o <output.png> <file/dir> [file/dir ...]
```

| Option | Required | Description |
|--------|----------|-------------|
| `-o <output.png>` | ✅ | Output PNG file |
| `-i <carrier.png>` | | Carrier image (auto-generated if omitted) |
| `<file/dir> ...` | ✅ | Files or directories to pack |

**Examples:**

```bash
# Pack with auto-generated carrier
./imgpacker.py pack -o backup.png ./my-app/

# Pack multiple files with custom carrier
./imgpacker.py pack -o backup.png -i photo.png .env config.json certs/

# Pack large directory
./imgpacker.py pack -o secrets.png -i carrier.png ~/private-data/

# Mix files and directories
./imgpacker.py pack -o archive.png file1.txt dir1/ file2.json dir2/
```

Output: PNG image file (~33% larger than compressed payload due to base64 encoding).

Packing shows:
- Every file being added (with full paths)
- Total file count
- Archive size (uncompressed)
- Compressed size and ratio
- Final encrypted payload size

### Extract files from image

```bash
./imgpacker.py extract <image.png>
```

Script will:
1. Load PNG image
2. Extract LSB data (2 bits per pixel)
3. Decode base64
4. **Prompt for password** (use same one from packing)
5. Decrypt with GPG
6. Decompress with xz
7. Extract tar to current directory
8. Exit

**Example:**

```bash
./imgpacker.py extract backup.png
# [imgpacker] Loading image: backup.png
# [imgpacker] Extracted 652 bytes from image
# [imgpacker] Decrypting payload...
# Enter passphrase for symmetric encryption: [type password]
# [imgpacker] Extracting files...
# [imgpacker]   ← file1.txt
# [imgpacker]   ← file2.txt
# [imgpacker]   ← subdir/nested.txt
# [imgpacker] Extracted 3 items
# [imgpacker] Done. Extracted to current directory.
```

---

## How it works

```
[Packing: imgpacker.py pack]
  ↓
  Input files/directories
  ↓
  tar (create archive)
  ↓
  xz (compress)
  ↓
  gpg --symmetric (encrypt w/ passphrase)
  ↓
  base64 (encode for embedding)
  ↓
  PNG LSB embedding (2 bits per pixel in R,G channels)
  ↓
  Output: image.png (looks innocent, contains encrypted payload)

[Extraction: imgpacker.py extract]
  ↓
  Load PNG image
  ↓
  Extract LSB from R,G channels (2 bits per pixel)
  ↓
  Reconstruct base64 payload
  ↓
  base64 -d (decode)
  ↓
  gpg --decrypt (decrypt w/ prompted password)
  ↓
  xz -d (decompress)
  ↓
  tar -x (extract to CWD)
  ↓
  Done
```

---

## Steganography Details

### PNG LSB Embedding

- **Bits per pixel:** 2 (embeds into R and G channels, leaves B and Alpha untouched)
- **Capacity:** ~1 byte per 4 pixels (or 1 MB per ~4 megapixel image)
- **Quality:** No visible image degradation; steganography is **reversible**
- **Robustness:** LSB is fragile to compression/resizing; keep original image format

### Example capacity:

| Image Size | Pixels  | Capacity |
|------------|---------|----------|
| 512×512    | 262k    | ~65 KB   |
| 1024×1024  | 1M      | ~250 KB  |
| 2048×2048  | 4M      | ~1 MB    |
| 4K (3840×2160) | 8.3M | ~2 MB   |

Actual capacity = `(width × height × 2 bits) / 8 - overhead (marker + size header)`

### Payload format:

```
[Marker (24 bytes)]
[Payload size (4 bytes, big-endian)]
[Encrypted base64 payload (variable)]
```

---

## Key points

- **Steganographic:** data hidden in image pixels, invisible to casual inspection
- **Self-contained:** single `.py` script
- **Portable:** Linux + macOS + Windows (WSL). Needs Python 3.7+
- **Secure:** GPG AES-256 symmetric encryption, passphrase-based. No key files exposed
- **Reversible:** LSB embedding doesn't corrupt image; extract losslessly
- **Minimal carrier:** auto-generates smallest PNG if no carrier image provided
- **Custom carrier:** can use existing photo/image (payload hidden in pixels)
- **Verbose:** detailed file listings and compression statistics

---

## Round-trip example

**On source machine (pack):**

```bash
mkdir -p confidential
echo "Secret data" > confidential/secrets.txt
echo '{"key": "value"}' > confidential/config.json

# Pack into image (will prompt for passphrase)
./imgpacker.py pack -o archive.png confidential/

# Output: archive.png (looks like normal PNG, contains encrypted payload)
file archive.png  # → PNG image data, 512 x 512, ...
```

**Transfer archive.png (HTTP, email, chat, etc.)**

```bash
curl -O http://example.com/archive.png
# or attach to email, post in Slack, etc.
```

**On target machine (extract):**

```bash
./imgpacker.py extract archive.png
# [imgpacker] Loading image: archive.png
# [imgpacker] Extracted 2048 bytes from image
# [imgpacker] Decrypting payload...
# Enter passphrase for symmetric encryption: [type password]
# [imgpacker] Extracting files...
# [imgpacker] Done. Extracted to current directory.

ls -la
# confidential/ directory with extracted files
```

---

## Carrier images

### Auto-generated carrier (default)

```bash
./imgpacker.py pack -o backup.png my-files/
```

Generates minimal solid-color PNG just large enough to hold payload. Fast, simple.

### Custom carrier

```bash
# Use existing photo as carrier
./imgpacker.py pack -o backup.png -i my-photo.jpg my-files/
```

Advantages:
- Image looks completely innocent (your real vacation photo)
- Plausible deniability (why would you hide data in a beach photo?)
- Larger capacity if carrier is big

Tradeoffs:
- Must have carrier image available at pack time
- Image must be large enough for payload (see capacity table above)
- LSB modifications invisible to human eye but detectable by steganalysis tools

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'PIL'"**
```bash
pip install Pillow
```

**"'xz' command not found"**
- xz not installed or not on PATH
- Linux: `apt install xz-utils`
- macOS: `brew install xz`
- Windows (WSL): `apt install xz-utils`

**"'gpg' command not found"**
- gpg not installed or not on PATH
- Linux: `apt install gnupg`
- macOS: `brew install gnupg`
- Windows: download from [gnupg.org](https://gnupg.org)

**"Payload too large for image"**
- Image doesn't have enough pixels for data
- Either: use larger carrier image, or compress payload (remove unnecessary files)
- Check capacity table above; resize or split data

**"Payload marker not found"**
- Image corrupted or not created by imgpacker
- Verify file wasn't modified during transfer (compare file size/hash)
- Re-pack and re-transfer

**"GPG decryption failed"**
- Wrong password or corrupted payload
- Password must match exactly (case-sensitive)
- Verify image file integrity: `file image.png` should show PNG header

---

## Security notes

- **Passphrase entry:** entered at terminal (not echoed), passes via stdin to gpg
- **Same passphrase:** required for both pack and extract operations
- **No temp files:** all operations via pipes; no intermediate files on disk
- **Encryption:** GPG AES-256 symmetric cipher (default, hardened)
- **Encoding:** base64 is NOT encryption; it's just ASCII-safe encoding for LSB embedding
- **Steganography is not encryption:** LSB embedding hides data but doesn't encrypt it
  - Combined with GPG encryption, this gives you both privacy (encryption) and deniability (steganography)
- **Steganalysis risk:** advanced tools can detect LSB modifications statistically
  - Defense: use large carrier images where LSB noise blends with natural image noise

---

## Tips

- **Carrier selection:** natural photos (clouds, grass, water) are better carriers than solid colors (steganalysis noise is less obvious)
- **File size:** base64 adds ~33% overhead; payload is further compressed with xz
- **Performance:** xz compression is slow on large files (can take minutes); it's worth it for final size
- **Batch operations:** extract multiple images by running script multiple times or looping
- **Verbose output:** packing and extraction show detailed file listings and compression statistics

---

## Examples

**Backup home directory covertly:**

```bash
./imgpacker.py pack -o family-photo.png -i my-vacation.png ~
# Transfer family-photo.png anywhere; looks like innocent vacation photo
# On target: ./imgpacker.py extract family-photo.png
```

**Package secrets for deployment:**

```bash
mkdir -p deploy-secrets
cp .env.production deploy-secrets/
cp cert.key deploy-secrets/
./imgpacker.py pack -o deploy.png deploy-secrets/

# Ship deploy.png to production server
# Server: ./imgpacker.py extract deploy.png (prompts for password)
```

**Share large encrypted data via image hosting:**

```bash
# Encrypt data into image
./imgpacker.py pack -o archive.png -i 4k-photo.png ~/large-dataset/

# Upload to imgur/photobucket/etc (looks like photo, encrypted payload hidden)
# Download and extract elsewhere
```

---

## Future enhancements

- Multi-image support (split large payloads across multiple images)
- JPEG support (DCT coefficient embedding, lossy)
- Checksum/integrity verification
- Progress bars for large operations
- Batch extraction from directory of images
- Web UI or GUI wrapper
