//! The Rust core must reproduce the project's golden vectors (`src/udeep/golden.json`, format 2), each case to
//! the tolerance the case itself states. Kinds this port does not implement are counted as skipped and printed,
//! never passed silently; the test fails if that list changes without this file being updated.

use std::{collections::BTreeMap, fs, path::PathBuf};

use serde_json::{Value, json};
use udeep_core::{
    Declaration, Error, Levels, Mapping, Noise, Stretch, Table, UNBOUNDED, caption, colours, declaration::exit_slope,
    description, digest_data, digest_pixels, float_text, format_g, iso21496_block, isolated, levels, levels_pq16, par, pq_encode,
    rounding_to_noise, scale, sha256, srgb_oetf, tone,
};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("src").join("udeep")
}

fn number(v: &Value) -> f64 {
    v.as_f64().unwrap_or(f64::NAN) // null is NaN
}

fn numbers(v: &Value) -> Vec<f64> {
    v.as_array().expect("a list").iter().map(number).collect()
}

fn out(v: f64) -> Value {
    if v.is_nan() { Value::Null } else { json!(v) }
}

fn list(values: impl IntoIterator<Item = f64>) -> Value {
    Value::Array(values.into_iter().map(out).collect())
}

fn table(file: &str) -> Vec<f32> {
    let bytes = fs::read(root().join("tables").join(file)).expect("a shipped table");
    bytes.as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)).collect()
}

fn mapping(m: &Value) -> Mapping {
    let exit = exit_slope(m["above_knee"].as_str().unwrap()).expect("a form above the knee this port reads");
    let record = Mapping {
        range_low: number(&m["range_low"]),
        range_high: number(&m["range_high"]),
        knee: number(&m["knee"]),
        reference_headroom: number(&m["reference_headroom"]),
        stretch_width: number(&m["stretch_width"]),
        stretch_offset: number(&m["stretch_offset"]),
        exit_slope: exit,
        stretch: Stretch::from_name(m["stretch"].as_str().unwrap()).unwrap() as u32,
        reserved: 0,
    };
    record.validate().expect("a valid mapping");
    record
}

fn declaration(d: &Value) -> Declaration {
    let text = |key: &str| d[key].as_str().unwrap_or_else(|| panic!("{key} is required")).to_string();
    let optional = |key: &str| d.get(key).and_then(Value::as_str).map(str::to_string);
    let count = |key: &str| d.get(key).and_then(Value::as_u64).unwrap_or(0);
    Declaration {
        data_unit: text("data_unit"),
        stretch: text("stretch"),
        range_low: number(&d["range_low"]),
        range_high: number(&d["range_high"]),
        knee: number(&d["knee"]),
        knee_rule: text("knee_rule"),
        reference_headroom: number(&d["reference_headroom"]),
        max_data_step: number(&d["max_data_step"]),
        noise_model: text("noise_model"),
        noise_parameter: d.get("noise_parameter").and_then(Value::as_f64),
        stretch_width: number(&d["stretch_width"]),
        stretch_offset: number(&d["stretch_offset"]),
        above_knee: text("above_knee"),
        masked: count("masked"),
        derived_mask: optional("derived_mask"),
        derived_masked: count("derived_masked"),
        record: text("record"),
        preset: optional("preset"),
        colour_map: optional("colour_map"),
        colour_map_table: optional("colour_map_table"),
        rounding_to_noise: d.get("rounding_to_noise").and_then(Value::as_f64),
        data_digest: optional("data_digest"),
        data_source: optional("data_source"),
        data_unit_code: optional("data_unit_code"),
        sdr_digest: optional("sdr_digest"),
        version: text("version"),
        image_state: text("image_state"),
    }
}

/// The result for one case, or None for a kind this port does not implement.
fn compute(case: &Value) -> Option<Value> {
    let a = &case["args"];
    Some(match case["kind"].as_str().unwrap() {
        "scale" => {
            let kind = Stretch::from_name(a["kind"].as_str().unwrap()).unwrap();
            let (lo, hi, width) = (number(&a["lo"]), number(&a["hi"]), number(&a["width"]));
            list(numbers(&a["x"]).into_iter().map(|x| scale(x, kind, lo, hi, width)))
        }
        "tone" => {
            let exit = a.get("exit_slope").map_or(1.0, number);
            list(numbers(&a["t"]).into_iter().map(|t| tone(t, number(&a["knee"]), number(&a["headroom"]), exit)))
        }
        "pq_encode" => list(numbers(&a["nits"]).into_iter().map(pq_encode)),
        "srgb_oetf" => list(numbers(&a["v"]).into_iter().map(srgb_oetf)),
        "cmap" => {
            let values = table(a["table"].as_str().unwrap());
            let t = Table::new(&values, number(&a["headroom"])).unwrap();
            Value::Array(numbers(&a["y"]).into_iter().map(|y| list(t.lookup(y))).collect())
        }
        "mapping" => {
            let m = mapping(&a["mapping"]);
            let values = case["tables"].as_array().unwrap().first().map(|f| table(f.as_str().unwrap()));
            let t = values.as_ref().map(|v| Table::new(v, m.reference_headroom).unwrap());
            let x = numbers(&a["x"]);
            let light: Vec<f64> = x.iter().map(|&v| m.light(v)).collect();
            let mut bulk = vec![0.0; x.len()];
            m.light_into(&x, &mut bulk); // the slice form must give the same numbers
            assert!(bulk.iter().zip(&light).all(|(p, q)| p.to_bits() == q.to_bits() || (p.is_nan() && q.is_nan())));
            json!({
                "knee_position": m.knee_position(),
                "position": list(x.iter().map(|&v| m.position(v))),
                "light": list(light.iter().copied()),
                "sdr_light": list(x.iter().map(|&v| m.sdr_light(v))),
                "value_of_light": list(light.iter().map(|&y| m.value(y))),
                "colours": Value::Array(light.iter().map(|&y| list(colours(y, t.as_ref()))).collect()),
            })
        }
        "mask_isolated" => {
            let rows: Vec<&str> = a["above"].as_array().unwrap().iter().map(|r| r.as_str().unwrap()).collect();
            let width = rows[0].len();
            let above: Vec<bool> = rows.iter().flat_map(|r| r.bytes().map(|c| c == b'1')).collect();
            let held = isolated(&above, width);
            Value::Array(
                held.chunks(width)
                    .map(|r| json!(r.iter().map(|&b| if b { '1' } else { '0' }).collect::<String>()))
                    .collect(),
            )
        }
        "iso21496" => {
            let block =
                iso21496_block(number(&a["gain_map_min"]), number(&a["gain_map_max"]), number(&a["hdr_capacity_max"]));
            json!(block.iter().map(|b| format!("{b:02x}")).collect::<String>())
        }
        "table" => {
            let bytes = fs::read(root().join("tables").join(a["file"].as_str().unwrap())).unwrap();
            json!({"sha256": sha256::digest(&bytes), "nodes": bytes.len() / 24, "bytes": bytes.len()})
        }
        "digest_data" => {
            let shape: Vec<usize> =
                a["shape"].as_array().unwrap().iter().map(|n| n.as_u64().unwrap() as usize).collect();
            let values: Vec<f64> = a["values"].as_array().unwrap().iter().flat_map(numbers).collect();
            json!(digest_data(&shape, &values))
        }
        "digest_pixels" => {
            let shape: Vec<usize> =
                a["shape"].as_array().unwrap().iter().map(|n| n.as_u64().unwrap() as usize).collect();
            let rows = a["values"].as_array().unwrap();
            let rgb: Vec<u8> =
                rows.iter().flat_map(|r| r.as_array().unwrap()).flat_map(numbers).map(|v| v as u8).collect();
            json!(digest_pixels(shape[0], shape[1], &rgb))
        }
        "levels" | "levels_pq16" => {
            let m = mapping(&a["mapping"]);
            let values = case["tables"].as_array().unwrap().first().map(|f| table(f.as_str().unwrap()));
            let t = values.as_ref().map(|v| Table::new(v, m.reference_headroom).unwrap());
            let samples = a["samples"].as_u64().unwrap() as usize;
            let found = if case["kind"] == "levels_pq16" {
                levels_pq16(&m, t.as_ref(), number(&a["white_nits"]), samples)
            } else {
                levels(&m, t.as_ref(), m.reference_headroom, samples)
            };
            let parameter = number(&a["noise"][1]);
            let noise = match a["noise"][0].as_str().unwrap() {
                "constant" => Noise::Constant(parameter),
                "poisson" => Noise::Poisson(parameter),
                "relative" => Noise::Relative(parameter),
                "tolerance" => Noise::Tolerance(parameter),
                other => panic!("a noise model this port does not have: {other}"),
            };
            let criterion = rounding_to_noise(&found, noise);
            // `at` is compared only in the cases that state it: those where one level is clearly the worst
            json!({
                "count": found.start.len(),
                "max_data_step": found.max_data_step(),
                "first_start": found.start[0],
                "rounding_to_noise": criterion.worst.min(UNBOUNDED),
                "failing_fraction": criterion.failing_fraction,
                "limit": criterion.limit,
                "meets": criterion.meets,
                "at": criterion.at,
            })
        }
        "xmp" => json!(description(&declaration(&a["declaration"])).expect("a valid declaration")),
        "caption" => json!(caption(&declaration(&a["declaration"]))),
        _ => return None,
    })
}

fn agree(got: &Value, want: &Value, case: &Value, at: &str, problems: &mut Vec<String>) {
    match want {
        Value::Object(map) => {
            for (k, w) in map {
                agree(got.get(k).unwrap_or(&Value::Null), w, case, &format!("{at}.{k}"), problems);
            }
        }
        Value::Array(items) => match got.as_array() {
            Some(g) if g.len() == items.len() => {
                for (i, (g, w)) in g.iter().zip(items).enumerate() {
                    agree(g, w, case, &format!("{at}[{i}]"), problems);
                }
            }
            _ => problems.push(format!("{at}: length differs")),
        },
        Value::Number(w) if case.get("exact").is_none() => {
            let (w, tol) = (w.as_f64().unwrap(), &case["tol"]);
            let ok = got.as_f64().is_some_and(|g| (g - w).abs() <= number(&tol["abs"]) + number(&tol["rel"]) * w.abs());
            if !ok {
                problems.push(format!("{at}: {got} != {w}"));
            }
        }
        _ => {
            if got != want {
                problems.push(format!("{at}: {got} != {want}"));
            }
        }
    }
}

#[test]
fn reproduces_the_golden_vectors() {
    let golden: Value = serde_json::from_str(&fs::read_to_string(root().join("golden.json")).unwrap()).unwrap();
    assert_eq!(golden["format"], 2, "this test reads golden format 2");
    let (mut passed, mut problems, mut skipped) = (0, Vec::new(), BTreeMap::<String, u32>::new());
    for (name, case) in golden["cases"].as_object().unwrap() {
        match compute(case) {
            None => *skipped.entry(case["kind"].as_str().unwrap().to_string()).or_default() += 1,
            Some(got) => {
                let before = problems.len();
                agree(&got, &case["out"], case, name, &mut problems);
                passed += usize::from(problems.len() == before);
            }
        }
    }
    println!("{passed} cases reproduced; skipped (not in this port): {skipped:?}");
    assert!(
        problems.is_empty(),
        "{} values out of tolerance, first: {:?}",
        problems.len(),
        &problems[..problems.len().min(5)]
    );
    // What this slice does not implement yet. Update this list when it shrinks.
    let not_yet: Vec<&str> = skipped.keys().map(String::as_str).collect();
    assert!(not_yet.is_empty(), "every kind of case is implemented; got {not_yet:?}");
    assert_eq!(passed, golden["cases"].as_object().unwrap().len(), "every case must be reproduced");
}

#[test]
fn the_comparison_is_not_vacuous() {
    let case = json!({"tol": {"rel": 1e-12, "abs": 1e-12}});
    let mut problems = Vec::new();
    agree(&json!([1.0, 2.0001]), &json!([1.0, 2.0]), &case, "x", &mut problems);
    agree(&Value::Null, &json!(1.0), &case, "nan", &mut problems);
    assert_eq!(problems.len(), 2);
}

#[test]
fn the_golden_file_holds_the_cases_this_test_was_written_for() {
    // If a kind of case lost its vectors, the test above would pass on fewer of them without saying so.
    let golden: Value = serde_json::from_str(&fs::read_to_string(root().join("golden.json")).unwrap()).unwrap();
    let mut kinds = BTreeMap::<&str, u32>::new();
    let mut models = BTreeMap::<&str, u32>::new();
    for case in golden["cases"].as_object().unwrap().values() {
        *kinds.entry(case["kind"].as_str().unwrap()).or_default() += 1;
        if case["kind"] == "levels" {
            *models.entry(case["args"]["noise"][0].as_str().unwrap()).or_default() += 1;
        }
    }
    let want = [
        ("caption", 1),
        ("cmap", 2),
        ("digest_data", 1),
        ("digest_pixels", 2),
        ("iso21496", 1),
        ("levels", 32),
        ("levels_pq16", 8),
        ("mapping", 8),
        ("mask_isolated", 1),
        ("pq_encode", 1),
        ("scale", 3),
        ("srgb_oetf", 1),
        ("table", 24),
        ("tone", 11),
        ("xmp", 2),
    ];
    assert_eq!(kinds, BTreeMap::from(want));
    assert_eq!(models, BTreeMap::from([("constant", 8), ("poisson", 8), ("relative", 8), ("tolerance", 8)]));
}

#[test]
fn a_record_that_cannot_define_a_mapping_is_refused() {
    let good = Mapping::new(Stretch::Linear, -160.0, 700.0, 240.0, 4.0);
    assert!(good.validate().is_ok());
    assert_eq!(std::mem::size_of::<Mapping>(), 64);
    for (bad, why) in [
        (Mapping { stretch: 9, ..good }, Error::UnknownStretch),
        (Mapping { range_high: -200.0, ..good }, Error::BadRange),
        (Mapping { stretch: Stretch::Log as u32, ..good }, Error::BadRange),
        (Mapping { knee: 900.0, ..good }, Error::KneeOutsideRange),
        (Mapping { reference_headroom: 0.5, ..good }, Error::BadHeadroom),
        (Mapping { exit_slope: 4.0, ..good }, Error::BadHeadroom),
        (Mapping { stretch_width: 0.0, ..good }, Error::BadStretchParameter),
        (Mapping { stretch: Stretch::Proportional as u32, ..good }, Error::BadStretchParameter),
    ] {
        assert_eq!(bad.validate(), Err(why));
    }
    assert!(Table::new(&[0.0; 5], 4.0).is_err());
}

#[test]
fn a_number_that_is_not_finite_is_refused_before_anything_else() {
    let good = Mapping::new(Stretch::Linear, -160.0, 700.0, 240.0, 4.0);
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        for record in [
            Mapping { range_low: bad, ..good },
            Mapping { range_high: bad, ..good }, // an infinite top passes range_high > range_low
            Mapping { knee: bad, ..good },
            Mapping { reference_headroom: bad, ..good }, // and an infinite headroom passes >= 1
            Mapping { stretch_width: bad, ..good },
            Mapping { stretch_offset: bad, ..good },
        ] {
            assert_eq!(record.validate(), Err(Error::NotFinite), "{record:?}");
        }
    }
    assert_eq!(Mapping { exit_slope: f64::NAN, ..good }.validate(), Err(Error::BadHeadroom));
}

#[test]
fn a_proportional_mapping_must_be_light_equal_to_value_over_knee() {
    let straight =
        Mapping { stretch: Stretch::Proportional as u32, ..Mapping::new(Stretch::Linear, 0.0, 12.0, 3.0, 4.0) };
    assert!(straight.validate().is_ok());
    for x in [0.0, 1.0, 3.0, 7.5, 12.0] {
        assert!((straight.light(x) - x / 3.0).abs() < 1e-12, "{x}");
    }
    // within the relative tolerance of 1e-9 the top still counts as knee x headroom
    assert!(Mapping { range_high: 12.0 * (1.0 + 5e-10), ..straight }.validate().is_ok());
    for bent in [
        Mapping { range_high: 10.0, ..straight },
        Mapping { range_high: 12.0 * (1.0 + 2e-9), ..straight },
        Mapping { reference_headroom: 2.0, ..straight },
        Mapping { exit_slope: 0.5, ..straight },
    ] {
        assert_eq!(bent.validate(), Err(Error::BadStretchParameter), "{bent:?}");
    }
    // the same numbers are a valid linear mapping: only the claim of proportionality is refused
    assert!(Mapping { range_high: 10.0, stretch: Stretch::Linear as u32, ..straight }.validate().is_ok());
}

#[test]
fn a_knee_the_curve_cannot_place_is_refused() {
    let unit = Mapping::new(Stretch::Linear, 0.0, 1.0, 0.5, 4.0);
    for knee in [1e-6, 0.5, 1.0 - 1e-6, 1.0] {
        assert!(Mapping { knee, ..unit }.validate().is_ok(), "{knee}");
    }
    for knee in [1e-9, 5e-7, 1.0 - 5e-7, 1.0 - 1e-9] {
        assert_eq!(Mapping { knee, ..unit }.validate(), Err(Error::KneeOutsideRange), "{knee}");
    }
    // the position is what counts, not the value: on a log scale this knee is 3.6e-11 of the way up the range
    let log = Mapping::new(Stretch::Log, 1.0, 1e12, 1.0 + 1e-9, 4.0);
    assert_eq!(log.validate(), Err(Error::KneeOutsideRange));
}

#[test]
fn levels_from_fewer_than_two_samples_are_those_from_two() {
    let m = Mapping::new(Stretch::Linear, 0.0, 1.0, 0.5, 4.0);
    let two = levels(&m, None, 4.0, 2);
    assert_eq!(two.start[0], 0.0);
    assert_eq!(two.width.iter().sum::<f64>(), 1.0);
    for samples in [0, 1] {
        assert_eq!(levels(&m, None, 4.0, samples), two, "samples = {samples}"); // samples - 1 used to underflow
    }
}

#[test]
fn noise_that_grows_from_zero_is_taken_at_the_end_of_a_level_nearest_zero() {
    let found = Levels { start: vec![-3.0, -1.0, 1.0, 2.0], width: vec![2.0, 2.0, 1.0, 4.0] };
    let error = |width: f64| width / 12f64.sqrt();
    // relative: sigma = 0.5 |x|, taken at x = -1, 0 (the level holds it), 1 and 2
    let relative = rounding_to_noise(&found, Noise::Relative(0.5));
    assert_eq!((relative.worst, relative.at, relative.meets), (f64::INFINITY, 0.0, false));
    assert!((relative.failing_fraction - 1.0).abs() < 1e-15);
    let above = Levels { start: found.start[2..].to_vec(), width: found.width[2..].to_vec() };
    let relative = rounding_to_noise(&above, Noise::Relative(0.5));
    assert_eq!((relative.worst, relative.at), (error(4.0) / 1.0, 2.0)); // not at the middle, 4, where it is half that
    // poisson: sigma = sqrt(4 x), taken at x = 1 and 2; below zero there is none
    let poisson = rounding_to_noise(&above, Noise::Poisson(4.0));
    assert_eq!((poisson.worst, poisson.at), (error(4.0) / 8f64.sqrt(), 2.0));
    assert_eq!(rounding_to_noise(&found, Noise::Poisson(4.0)).at, -1.0); // the first of the levels with no noise
    // constant noise and a tolerance are the same everywhere: reported at the middle of the widest level
    let constant = rounding_to_noise(&found, Noise::Constant(100.0));
    assert_eq!((constant.worst, constant.at, constant.limit, constant.meets), (error(4.0) / 100.0, 4.0, 0.1, true));
    assert_eq!(constant.failing_fraction, 0.0);
    let tolerance = rounding_to_noise(&found, Noise::Tolerance(1.5));
    assert_eq!((tolerance.worst, tolerance.at, tolerance.limit, tolerance.meets), (2.0 / 1.5, 4.0, 1.0, false));
    assert!((tolerance.failing_fraction - 4.0 / 9.0).abs() < 1e-15);
}

#[test]
fn a_value_with_no_shape_is_digested_as_the_reference_digests_a_scalar() {
    // udeep.metadata.digest_data(numpy.array(1.5)), which is also its digest of [1.5]
    let scalar = "sha256:0931b8449e03fa553804fc425b21f29865b97e5d46ce7cf6961f83ac61783909";
    assert_eq!(digest_data(&[], &[1.5]), scalar);
    assert_eq!(digest_data(&[1], &[1.5]), scalar);
    assert_ne!(digest_data(&[1, 1], &[1.5]), scalar); // a shape that is stated still counts
    // and an empty array is as it was: udeep.metadata.digest_data(numpy.zeros(0))
    assert_eq!(digest_data(&[0], &[]), "sha256:06d633bdb7b9b775e73d8bbb9f92421b83670ba9084512dc4f21042e523ac8f2");
}

#[test]
fn one_thread_is_the_callers_own() {
    use std::{collections::BTreeSet, sync::Mutex, thread};
    let ran_on = |n: usize, threads: usize| {
        let seen = Mutex::new((BTreeSet::new(), Vec::new()));
        par::in_ranges(n, threads, |range| {
            let mut seen = seen.lock().unwrap();
            seen.0.insert(format!("{:?}", thread::current().id()));
            seen.1.push(range);
        });
        let (ids, mut ranges) = seen.into_inner().unwrap();
        ranges.sort_by_key(|r| r.start);
        (ids, ranges)
    };
    let here = BTreeSet::from([format!("{:?}", thread::current().id())]);
    for n in [1000, 0] {
        let (ids, ranges) = ran_on(n, 1);
        assert_eq!(ids, here, "n = {n}");
        assert!(ranges.len() == 1 && ranges[0] == (0..n), "{ranges:?}");
    }
    // several threads: none is the caller's, and their ranges cover every index once
    let (ids, ranges) = ran_on(1001, 4);
    assert!(ids.len() > 1 && ids.is_disjoint(&here), "{ids:?}");
    assert_eq!(ranges, vec![0..251, 251..502, 502..753, 753..1001]);
}

fn valid_declaration() -> Declaration {
    Declaration {
        data_unit: "K".into(),
        stretch: "linear".into(),
        range_low: 0.0,
        range_high: 100.0,
        knee: 50.0,
        knee_rule: "physical; the melting point".into(),
        reference_headroom: 4.0,
        max_data_step: 0.5,
        noise_model: "constant".into(),
        noise_parameter: Some(2.0),
        stretch_width: 1.0,
        stretch_offset: 0.0,
        above_knee: "hermite; exit=1".into(),
        masked: 0,
        derived_mask: None,
        derived_masked: 0,
        record: "rendering".into(),
        preset: None,
        colour_map: Some("grey".into()),
        colour_map_table: None,
        rounding_to_noise: Some(0.07),
        data_digest: None,
        data_source: None,
        data_unit_code: None,
        sdr_digest: None,
        version: "0.3".into(),
        image_state: "data-referred".into(),
    }
}

#[test]
fn a_declaration_the_reference_would_refuse_is_not_written() {
    let good = valid_declaration();
    assert_eq!(good.validate(), Ok(()));
    assert!(description(&good).unwrap().contains(r#" udeep:RoundingToNoise="0.07""#));
    let text = |s: &str| s.to_string();
    for (bad, names) in [
        (Declaration { version: text("0.2"), ..good.clone() }, "version"),
        (Declaration { image_state: text("display-referred"), ..good.clone() }, "image_state"),
        (Declaration { max_data_step: 0.0, ..good.clone() }, "max_data_step"),
        (Declaration { max_data_step: f64::NAN, ..good.clone() }, "max_data_step"),
        (Declaration { max_data_step: f64::INFINITY, ..good.clone() }, "max_data_step"),
        (Declaration { noise_parameter: Some(-1.0), ..good.clone() }, "noise_parameter"),
        (Declaration { noise_parameter: Some(f64::INFINITY), ..good.clone() }, "noise_parameter"),
        (Declaration { noise_parameter: None, ..good.clone() }, "noise_parameter"),
        (Declaration { rounding_to_noise: Some(-0.1), ..good.clone() }, "rounding_to_noise"),
        (Declaration { rounding_to_noise: Some(f64::NAN), ..good.clone() }, "rounding_to_noise"),
        (Declaration { rounding_to_noise: Some(f64::INFINITY), ..good.clone() }, "rounding_to_noise"),
        (Declaration { derived_masked: 3, ..good.clone() }, "derived_mask"),
        (Declaration { derived_masked: 3, derived_mask: Some(String::new()), ..good.clone() }, "derived_mask"),
        (Declaration { data_unit: String::new(), ..good.clone() }, "data_unit"),
        (Declaration { noise_model: text("gaussian"), ..good.clone() }, "noise_model"),
        (Declaration { knee_rule: text("by eye"), ..good.clone() }, "knee_rule"),
        (Declaration { record: text("archive"), ..good.clone() }, "record"),
        (Declaration { reference_headroom: 1.0, ..good.clone() }, "reference_headroom"),
        (Declaration { range_high: f64::INFINITY, ..good.clone() }, "finite"),
        (Declaration { knee: 200.0, ..good.clone() }, "knee"),
        (Declaration { stretch: text("sqrt"), ..good.clone() }, "stretch"),
        (Declaration { above_knee: text("hermite; exit=3.5"), ..good.clone() }, "above the knee"),
        (Declaration { above_knee: text("hermite; exit=1.2.3"), ..good.clone() }, "above the knee"),
        (Declaration { above_knee: text("hermite; exit=1e0"), ..good.clone() }, "above the knee"),
        (Declaration { above_knee: text("spline"), ..good.clone() }, "above the knee"),
    ] {
        let why = description(&bad).expect_err("a declaration that must be refused");
        assert!(why.contains(names), "{why:?} should name {names}");
    }
    let unknown = Declaration { noise_model: text("unknown"), noise_parameter: None, ..good.clone() };
    assert_eq!(unknown.validate(), Ok(()));
    let record = Declaration { record: text("data-record"), ..unknown };
    assert!(record.validate().unwrap_err().contains("data-record"));
    let held = Declaration { derived_masked: 3, derived_mask: Some(text("isolated-3x3")), ..good };
    assert_eq!(held.validate(), Ok(()));
}

#[test]
fn the_exit_slope_is_read_as_the_reference_reads_it() {
    for (text, want) in [
        ("hermite; exit=1", Some(1.0)),
        ("hermite; exit=2", Some(2.0)),
        ("hermite; exit=0.5", Some(0.5)),
        ("hermite; exit=0", Some(0.0)),
        ("hermite; exit=3", Some(3.0)),
        ("hermite; exit=1.", Some(1.0)),
        ("hermite; exit=.5", Some(0.5)),
        ("hermite; exit=3.5", None),
        ("hermite; exit=", None),
        ("hermite; exit=.", None),
        ("hermite; exit=1.2.3", None),
        ("hermite; exit=-1", None),
        ("hermite; exit=+1", None),
        ("hermite; exit=1 ", None),
        ("hermite; exit=inf", None),
        ("hermite;exit=1", None),
    ] {
        assert_eq!(exit_slope(text), want, "{text:?}");
    }
}

#[test]
fn a_float_is_written_as_the_shortest_text_that_reads_back() {
    // each as udeep.metadata.float_text writes it: Python's repr, without the ".0" of a whole number
    for (value, want) in [
        (0.0, "0"),
        (-0.0, "-0"),
        (4.0, "4"),
        (-160.0, "-160"),
        (0.1, "0.1"),
        (0.1 + 0.2, "0.30000000000000004"),
        (189.70123456789, "189.70123456789"),
        (1700000002.3456, "1700000002.3456"),
        (1.0 / 3.0, "0.3333333333333333"),
        (-1.0 / 3.0, "-0.3333333333333333"),
        (16383.0, "16383"),
        (1e15, "1000000000000000"),
        (9007199254740992.0, "9007199254740992"),
        (1e16, "1e+16"),
        (123456789012345680.0, "1.2345678901234568e+17"),
        (1e22, "1e+22"),
        (0.0001, "0.0001"),
        (0.00012345, "0.00012345"),
        (0.00001, "1e-05"),
        (1.5e-7, "1.5e-07"),
        (-2.5e-10, "-2.5e-10"),
        (1e100, "1e+100"),
        // exactly half way between two shortest texts: Python takes the even last digit, where Rust's `{}` takes
        // the upper ("...529.3"); and the odd neighbour, where the two agree
        // (written as sums, which are exact here, because a literal with the quarter is more digits than Rust needs)
        (884938148863529.0 + 0.25, "884938148863529.2"),
        (-1633218193966616.0 - 0.25, "-1633218193966616.2"),
        (884938148863529.0 + 0.75, "884938148863529.8"),
        (f64::MAX, "1.7976931348623157e+308"),
        (5e-324, "5e-324"),
        (f64::MIN_POSITIVE, "2.2250738585072014e-308"),
    ] {
        let text = float_text(value).expect("a finite number");
        assert_eq!(text, want, "{value:e}");
        assert_eq!(text.parse::<f64>().unwrap().to_bits(), value.to_bits(), "{text} must read back");
    }
    for cannot in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert_eq!(float_text(cannot), None);
    }
}

#[cfg(feature = "jpeg")]
#[test]
fn the_writer_refuses_a_value_too_large_for_a_gain_and_writes_what_has_no_light_as_zero() {
    use udeep_core::gainmap::{WriteError, write_gainmap_jpeg};
    let sdr = vec![0.5; 8 * 8 * 3];
    let hdr = vec![1.0; 8 * 8 * 3];
    let write = |hdr: &[f64]| write_gainmap_jpeg(&sdr, hdr, 8, 8, 4.0, 95, 100, "");
    assert!(write(&hdr).is_ok());
    // infinite, or finite but so large that its ratio to the base overflows: no gain can be stored for it
    for too_large in [f64::INFINITY, f64::MAX] {
        let mut bad = hdr.clone();
        bad[100] = too_large;
        assert!(matches!(write(&bad), Err(WriteError::Range)), "{too_large}");
    }
    // NaN is a pixel with no data, and minus infinity is below black: both are written as 0, as the reference
    // writes them, and the gain range stays finite
    for nothing in [f64::NAN, f64::NEG_INFINITY] {
        let mut holed = hdr.clone();
        holed[100] = nothing;
        let file = write(&holed).expect("a file");
        let text = String::from_utf8_lossy(&file);
        assert!(text.contains("hdrgm:GainMapMax=\""), "{nothing}");
        assert!(!text.contains("=\"inf\"") && !text.contains("=\"nan\"") && !text.contains("=\"NaN\""), "{nothing}");
    }
}

#[test]
fn sha256_matches_the_published_test_vectors() {
    // FIPS 180-4 examples, and a message long enough to span several blocks
    assert_eq!(sha256::digest(b""), "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert_eq!(sha256::digest(b"abc"), "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert_eq!(
        sha256::digest(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        "sha256:248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
    );
    assert_eq!(
        sha256::digest(&vec![b'a'; 1_000_000]),
        "sha256:cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
    );
}

#[test]
fn the_parallel_loop_gives_the_same_bits_as_the_serial_one() {
    let m = Mapping { stretch_width: 0.1, ..Mapping::new(Stretch::Asinh, 0.0, 20.0, 1.2, 4.0) };
    let x: Vec<f64> =
        (0..200_003).map(|i| if i % 9973 == 0 { f64::NAN } else { -1.0 + 22.0 * i as f64 / 200_003.0 }).collect();
    let mut serial = vec![0.0; x.len()];
    m.light_into(&x, &mut serial);
    for threads in [0, 1, 2, 3, 7] {
        let mut parallel = vec![0.0; x.len()];
        udeep_core::par::light_into(&m, &x, &mut parallel, threads);
        assert!(serial.iter().zip(&parallel).all(|(a, b)| a.to_bits() == b.to_bits()), "threads = {threads}");
    }
    assert_eq!(udeep_core::par::threads_for(8, 1000), 1); // too few values to be worth a second thread
    assert_eq!(udeep_core::par::threads_for(8, 16_384 * 3), 3);
}

#[test]
fn numbers_are_written_as_c_and_python_write_them_with_g() {
    for (value, precision, want) in [
        (0.0, 10, "0"),
        (4.0, 10, "4"),
        (-160.0, 10, "-160"),
        (189.70123456789, 10, "189.7012346"),
        (0.1 + 0.2, 10, "0.3"),
        (1e16, 10, "1e+16"),
        (123456789012.0, 10, "1.23456789e+11"),
        (1234567890.0, 10, "1234567890"),
        (0.0001, 10, "0.0001"),
        (0.00001, 10, "1e-05"),
        (1.5e-3, 10, "0.0015"),
        (2625.5, 4, "2626"),
        (16383.0, 4, "1.638e+04"),
        (16383.0, 6, "16383"),
        (189.70123456789, 6, "189.701"),
        (1234567.5, 6, "1.23457e+06"),
        (0.256, 2, "0.26"),
        (0.1, 6, "0.1"),
        (f64::INFINITY, 4, "inf"),
    ] {
        assert_eq!(format_g(value, precision), want, "{value} at {precision}");
    }
}

#[test]
fn the_caption_never_shows_a_digit_the_number_does_not_have() {
    // The reference's rule, with its own outputs: six figures, in full only below a million.
    let d = Declaration { range_high: 12_345_678.0, knee: 1_234_567.0, ..valid_declaration() };
    let text = caption(&d);
    assert!(text.contains("to 1.23457e+07"), "{text}");
    assert!(text.contains("White is at 1.23457e+06"), "{text}");
    let d = Declaration { range_high: 999_999.0, knee: 16_383.0, ..valid_declaration() };
    let text = caption(&d);
    assert!(text.contains("to 999999") && text.contains("White is at 16383"), "{text}");
}

#[test]
fn a_knee_exactly_at_the_limit_is_accepted_whatever_the_rounding() {
    // the reviewer's case: this knee is at position 1e-6 and works out as 9.999999999999555e-07
    let (lo, hi) = (-73.12715117751975, 42281.770091456114);
    assert!(Mapping::new(Stretch::Linear, lo, hi, -73.08479628027712, 4.0).validate().is_ok());
    assert!(Mapping::new(Stretch::Linear, 0.0, 1.0, 5e-7, 4.0).validate().is_err());
    assert!(Mapping::new(Stretch::Linear, 0.0, 1.0, 1.0 - 5e-7, 4.0).validate().is_err());
}
