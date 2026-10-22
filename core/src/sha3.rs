//! SHA3-256 (FIPS 202), written out here so that the core keeps no dependencies. The digests are taken with it as
//! well as with SHA-256 (profile 8.6) so that the link from a file to its data does not rest on one hash
//! function: SHA-3 is a sponge built on the Keccak permutation, of a design unrelated to SHA-2's. Not written to
//! be fast.

/// The round constants of Keccak-f[1600].
const RC: [u64; 24] = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808a,
    0x8000000080008000,
    0x000000000000808b,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008a,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000a,
    0x000000008000808b,
    0x800000000000008b,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800a,
    0x800000008000000a,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
];
/// The rotation of each lane (rho), in the order the lanes are visited when they are moved (pi).
const ROTATION: [u32; 24] = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44];
/// Where each lane moves to (pi), as an index into the state, `x + 5 y`.
const MOVE: [usize; 24] = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];
/// The bytes absorbed per permutation for SHA3-256: 1600 bits less twice the 256-bit output.
const RATE: usize = 136;

/// Keccak-f[1600]: 24 rounds of theta, rho and pi, chi and iota over 25 lanes of 64 bits.
fn permute(state: &mut [u64; 25]) {
    for rc in RC {
        let mut column = [0u64; 5];
        for (x, c) in column.iter_mut().enumerate() {
            *c = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
        }
        for x in 0..5 {
            let t = column[(x + 4) % 5] ^ column[(x + 1) % 5].rotate_left(1);
            for y in (0..25).step_by(5) {
                state[y + x] ^= t;
            }
        }
        let mut carried = state[1];
        for (&to, &by) in MOVE.iter().zip(&ROTATION) {
            let next = state[to];
            state[to] = carried.rotate_left(by);
            carried = next;
        }
        for y in (0..25).step_by(5) {
            let row = [state[y], state[y + 1], state[y + 2], state[y + 3], state[y + 4]];
            for x in 0..5 {
                state[y + x] = row[x] ^ (!row[(x + 1) % 5] & row[(x + 2) % 5]);
            }
        }
        state[0] ^= rc;
    }
}

/// A running SHA3-256.
pub struct Sha3_256 {
    state: [u64; 25],
    block: [u8; RATE],
    filled: usize,
}

impl Default for Sha3_256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha3_256 {
    pub fn new() -> Self {
        Self { state: [0; 25], block: [0; RATE], filled: 0 }
    }

    /// Absorb the block: its bytes, eight to a lane, least significant first.
    fn absorb(&mut self) {
        for (lane, bytes) in self.state.iter_mut().zip(self.block.as_chunks::<8>().0) {
            *lane ^= u64::from_le_bytes(*bytes);
        }
        permute(&mut self.state);
    }

    pub fn update(&mut self, mut data: &[u8]) {
        while !data.is_empty() {
            let take = (RATE - self.filled).min(data.len());
            self.block[self.filled..self.filled + take].copy_from_slice(&data[..take]);
            self.filled += take;
            data = &data[take..];
            if self.filled == RATE {
                self.absorb();
                self.filled = 0;
            }
        }
    }

    /// The digest as 64 lower-case hex digits. The padding is SHA-3's: the bits `01` that mark the function, then
    /// `1`, zeros and a final `1` (FIPS 202, 5.1 and 6.1), which is the byte 0x06 and a top bit at the block's end.
    pub fn hex(mut self) -> String {
        self.block[self.filled..].fill(0);
        self.block[self.filled] ^= 0x06;
        self.block[RATE - 1] ^= 0x80;
        self.absorb();
        self.state[..4].iter().flat_map(|lane| lane.to_le_bytes()).map(|b| format!("{b:02x}")).collect()
    }
}

/// `"sha3-256:"` and the digest of `data`.
pub fn digest(data: &[u8]) -> String {
    let mut h = Sha3_256::new();
    h.update(data);
    format!("sha3-256:{}", h.hex())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// FIPS 202's examples and NIST's test messages, and lengths either side of a block, against Python's hashlib.
    #[test]
    fn known_digests() {
        let a3 = [0xa3u8; 200];
        let cases: [(&[u8], &str); 7] = [
            (b"", "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a"),
            (b"abc", "3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532"),
            (
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
                "41c0dba2a9d6240849100376a8235e2c82e1b9998a999e21db32dd97496d3376",
            ),
            (&a3, "79f38adec5c20307a98ef76e8324afbfd46cfd81b22e3973c65fa1bd9de31787"),
            (&[b'a'; 135], "8094bb53c44cfb1e67b7c30447f9a1c33696d2463ecc1d9c92538913392843c9"),
            (&[b'a'; 136], "3fc5559f14db8e453a0a3091edbd2bc25e11528d81c66fa570a4efdcc2695ee1"),
            (&[b'a'; 137], "f8d6846cedd2ccfadf15c5879ef95af724d799eed7391fb1c91f95344e738614"),
        ];
        for (message, expected) in cases {
            assert_eq!(digest(message), format!("sha3-256:{expected}"), "{} bytes", message.len());
        }
    }

    /// Fed in pieces of any size, the digest is the digest of the whole.
    #[test]
    fn pieces_give_the_whole() {
        let message: Vec<u8> = (0..1000u32).map(|i| (i * 7 % 251) as u8).collect();
        for size in [1, 7, 135, 136, 137, 999] {
            let mut h = Sha3_256::new();
            message.chunks(size).for_each(|piece| h.update(piece));
            assert_eq!(format!("sha3-256:{}", h.hex()), digest(&message), "pieces of {size}");
        }
    }
}
