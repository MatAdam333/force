//! Minimal PNG writer (RGB8, uncompressed deflate) for exporting stills without dependencies.

use std::io::{self, Write};

fn crc32(data: &[u8], mut crc: u32) -> u32 {
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xedb8_8320
            } else {
                crc >> 1
            };
        }
    }
    crc
}

fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let crc = crc32(data, crc32(kind, 0xffff_ffff)) ^ 0xffff_ffff;
    out.extend_from_slice(&crc.to_be_bytes());
}

/// Encodes `rgb` (w * h * 3 bytes, row-major) as a PNG.
pub fn encode(w: usize, h: usize, rgb: &[u8]) -> Vec<u8> {
    assert_eq!(rgb.len(), w * h * 3);
    let mut raw = Vec::with_capacity((w * 3 + 1) * h);
    for row in rgb.chunks(w * 3) {
        raw.push(0); // filter: none
        raw.extend_from_slice(row);
    }
    // zlib stream of stored (uncompressed) deflate blocks
    let mut z = vec![0x78, 0x01];
    let mut blocks = raw.chunks(65535).peekable();
    while let Some(b) = blocks.next() {
        z.push(if blocks.peek().is_none() { 1 } else { 0 });
        z.extend_from_slice(&(b.len() as u16).to_le_bytes());
        z.extend_from_slice(&(!(b.len() as u16)).to_le_bytes());
        z.extend_from_slice(b);
    }
    let (mut a, mut b) = (1u32, 0u32);
    for &x in &raw {
        a = (a + x as u32) % 65521;
        b = (b + a) % 65521;
    }
    z.extend_from_slice(&((b << 16) | a).to_be_bytes());

    let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(w as u32).to_be_bytes());
    ihdr.extend_from_slice(&(h as u32).to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &ihdr);
    chunk(&mut out, b"IDAT", &z);
    chunk(&mut out, b"IEND", &[]);
    out
}

pub fn write(path: &std::path::Path, w: usize, h: usize, rgb: &[u8]) -> io::Result<()> {
    std::fs::File::create(path)?.write_all(&encode(w, h, rgb))
}

#[cfg(test)]
mod tests {
    #[test]
    fn crc_matches_reference() {
        // CRC-32 of "IEND" is the well-known AE426082
        assert_eq!(
            super::crc32(b"IEND", 0xffff_ffff) ^ 0xffff_ffff,
            0xae42_6082
        );
    }

    #[test]
    fn png_has_signature_and_chunks() {
        let png = super::encode(2, 2, &[255; 12]);
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert!(png.windows(4).any(|w| w == b"IDAT"));
        assert!(png.ends_with(&[0xae, 0x42, 0x60, 0x82]));
    }
}
