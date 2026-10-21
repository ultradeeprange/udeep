//! The generator against the published tables it was ported from: every registered map, version and headroom,
//! made again here at the published node count. The published table is the definition (profile 6.10), so this
//! checks the port, not the tables: a table this crate makes is carried by the file that uses it.

use std::path::PathBuf;

use udeep_core::cmap;

fn tables() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../src/udeep/tables")
}

/// Largest difference between a made table and a published one, in multiples of white: the version-3 floor, below
/// which a value is stored as zero. On Windows, on 2026-10-21, versions 1 and 3 were made again bit for bit and
/// version 2 to within 6e-13 of white, in the values almost zero that profile 6.10 describes.
const TOLERANCE: f64 = 1e-7;

#[test]
fn the_generator_makes_every_published_table_again() {
    let index: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(tables().join("index.json")).unwrap()).unwrap();
    let nodes = index["nodes"].as_u64().unwrap() as usize;
    let mut checked = 0;
    for (key, entry) in index["tables"].as_object().unwrap() {
        let (map, _) = key.split_once('@').unwrap();
        let (name, version) = map.split_once('/').unwrap();
        let headroom = entry["headroom"].as_f64().unwrap();
        let bytes = std::fs::read(tables().join(entry["file"].as_str().unwrap())).unwrap();
        let published: Vec<f32> = bytes.as_chunks::<4>().0.iter().map(|&b| f32::from_le_bytes(b)).collect();
        let made = cmap::generate(name, version.parse().unwrap(), headroom, nodes).unwrap();
        assert_eq!(made.len(), published.len(), "{key}");
        let worst = made.iter().zip(&published).map(|(a, b)| f64::from((a - b).abs())).fold(0.0, f64::max);
        let same = made.iter().zip(&published).filter(|(a, b)| a.to_bits() == b.to_bits()).count();
        println!("{key}: largest difference {worst:.3e}, {same} of {} numbers the same to the bit", made.len());
        assert!(worst <= TOLERANCE, "{key}: {worst:e}");
        checked += 1;
    }
    assert_eq!(checked, 24);
}
