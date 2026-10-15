use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::{HashMap, HashSet};

type Field = (
    String,
    String,
    bool,
    String,
    Vec<u8>,
    Vec<Vec<u8>>,
    Vec<(Vec<u8>, Vec<u8>)>,
);
type Review = (
    String,
    Vec<u8>,
    Vec<Vec<u8>>,
    Option<Vec<u8>>,
    Option<Vec<u8>>,
);

fn result(tag: &str, text: Vec<u8>, display: Option<Vec<u8>>, file: Option<Vec<u8>>) -> Review {
    (tag.to_owned(), text, Vec::new(), display, file)
}

// Match CPython whitespace without UTF-8 decoding: surrogatepass payloads
// contain arbitrary Python code points and must round-trip unchanged.
fn trim(value: &[u8]) -> Vec<u8> {
    const SPACE: &[&[u8]] = &[
        b"\t",
        b"\n",
        b"\x0b",
        b"\x0c",
        b"\r",
        b"\x1c",
        b"\x1d",
        b"\x1e",
        b"\x1f",
        b" ",
        b"\xc2\x85",
        b"\xc2\xa0",
        b"\xe1\x9a\x80",
        b"\xe2\x80\x80",
        b"\xe2\x80\x81",
        b"\xe2\x80\x82",
        b"\xe2\x80\x83",
        b"\xe2\x80\x84",
        b"\xe2\x80\x85",
        b"\xe2\x80\x86",
        b"\xe2\x80\x87",
        b"\xe2\x80\x88",
        b"\xe2\x80\x89",
        b"\xe2\x80\x8a",
        b"\xe2\x80\xa8",
        b"\xe2\x80\xa9",
        b"\xe2\x80\xaf",
        b"\xe2\x81\x9f",
        b"\xe3\x80\x80",
    ];
    let mut remaining = value;
    while let Some(space) = SPACE.iter().find(|space| remaining.starts_with(space)) {
        remaining = &remaining[space.len()..];
    }
    while let Some(space) = SPACE.iter().find(|space| remaining.ends_with(space)) {
        remaining = &remaining[..remaining.len() - space.len()];
    }
    remaining.to_vec()
}

fn review(field: Field) -> Review {
    let (key, kind, required, tag, text, items, options) = field;
    let error =
        |message: &str| result("error", format!("{key} {message}").into_bytes(), None, None);
    if tag == "none" || (tag == "text" && text.is_empty()) || (tag == "tuple" && items.is_empty()) {
        return if required {
            error("is required")
        } else {
            result("blank", Vec::new(), None, None)
        };
    }
    match kind.as_str() {
        "text" | "long_text" | "phone" => {
            if tag != "text" {
                return error("must be text");
            }
            let value = trim(&text);
            result("text", value.clone(), Some(value), None)
        }
        "email" | "url" | "number" | "date" => result("parser", Vec::new(), None, None),
        "single_choice" | "dropdown" => {
            let options: HashMap<_, _> = options.into_iter().collect();
            if tag != "text" || !options.contains_key(&text) {
                return error("must name a declared option");
            }
            let display = options.get(&text).cloned();
            result("text", text, display, None)
        }
        "multi_choice" => {
            let options: HashMap<_, _> = options.into_iter().collect();
            if tag != "tuple"
                || items.is_empty()
                || items.iter().any(|item| !options.contains_key(item))
            {
                return error("must name declared options");
            }
            let mut seen = HashSet::new();
            let selected: Vec<_> = items
                .into_iter()
                .filter(|item| seen.insert(item.clone()))
                .collect();
            let display = selected
                .iter()
                .map(|item| options[item].as_slice())
                .collect::<Vec<_>>()
                .join(b", ".as_slice());
            (
                "multi".to_owned(),
                Vec::new(),
                selected,
                Some(display),
                None,
            )
        }
        "checkbox" | "yes_no" | "consent" => {
            if tag != "bool" {
                return error("must be true or false");
            }
            let display = if text == b"true" { "Yes" } else { "No" };
            result("bool", text, Some(display.as_bytes().to_vec()), None)
        }
        "rating" => {
            if tag != "int" || !matches!(text.as_slice(), b"1" | b"2" | b"3" | b"4" | b"5") {
                return error("must be a rating from 1 to 5");
            }
            result("rating", text.clone(), Some(text), None)
        }
        "file" | "image" | "pdf" => {
            let value = trim(&text);
            if tag != "text" || value.is_empty() {
                return error("must be an opaque file reference");
            }
            result("text", value.clone(), Some(value.clone()), Some(value))
        }
        _ => result(
            "error",
            format!("unsupported field type {kind}").into_bytes(),
            None,
            None,
        ),
    }
}

type PythonReview = (
    String,
    Py<PyBytes>,
    Vec<Py<PyBytes>>,
    Option<Py<PyBytes>>,
    Option<Py<PyBytes>>,
);

fn byte_payload(py: Python<'_>, value: Vec<u8>) -> Py<PyBytes> {
    PyBytes::new(py, &value).unbind()
}

#[pyfunction]
fn review_answers(py: Python<'_>, fields: Vec<Field>) -> Vec<PythonReview> {
    let results: Vec<Review> = py.detach(move || fields.into_iter().map(review).collect());
    results
        .into_iter()
        .map(|(tag, text, items, display, file)| {
            (
                tag,
                byte_payload(py, text),
                items
                    .into_iter()
                    .map(|value| byte_payload(py, value))
                    .collect(),
                display.map(|value| byte_payload(py, value)),
                file.map(|value| byte_payload(py, value)),
            )
        })
        .collect()
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(review_answers, module)?)?;
    Ok(())
}
