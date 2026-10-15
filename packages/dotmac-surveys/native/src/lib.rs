//! Bulk response validation over reversible Python Unicode bytes and indices.

use std::collections::{BTreeMap, BTreeSet};

use pyo3::prelude::*;

// Kind values: 0 free text, 1 rating, 2 NPS, 3 multiple choice.
// Values are already stripped with Python's Unicode semantics at the bridge.
type Question = (Vec<u8>, u8, bool, Vec<Vec<u8>>);
type Answer = (Vec<u8>, Vec<u8>, usize);
type Error = (String, Vec<usize>);
type Result = (Vec<(usize, usize)>, Option<u8>, Option<u8>, Option<Error>);

fn refusal(code: &str, indices: Vec<usize>) -> Result {
    (Vec::new(), None, None, Some((code.to_owned(), indices)))
}

fn canonical_number(value: &[u8], maximum: u8) -> Option<u8> {
    // Whitelist the canonical ASCII spelling. Parsing Unicode digits, signs,
    // underscores or large integers cannot make a noncanonical token valid.
    let numeric = match value {
        [digit @ b'0'..=b'9'] => *digit - b'0',
        [b'1', b'0'] => 10,
        _ => return None,
    };
    (numeric <= maximum).then_some(numeric)
}

fn validate(questions: Vec<Question>, answers: Vec<Answer>) -> Result {
    let known: BTreeSet<&[u8]> = questions
        .iter()
        .map(|question| question.0.as_slice())
        .collect();
    let submitted: BTreeMap<&[u8], usize> = answers
        .iter()
        .enumerate()
        .map(|(index, answer)| (answer.0.as_slice(), index))
        .collect();
    // UTF-8 with surrogatepass preserves codepoint lexicographic ordering,
    // including isolated surrogates, without requiring Rust Unicode strings.
    let unknown: Vec<usize> = submitted
        .iter()
        .filter_map(|(key, &index)| (!known.contains(*key)).then_some(index))
        .collect();
    if !unknown.is_empty() {
        return refusal("unknown", unknown);
    }
    let mut normalized = Vec::new();
    let mut rating = None;
    let mut nps = None;
    for (question_index, question) in questions.iter().enumerate() {
        let submitted_index = submitted.get(question.0.as_slice()).copied();
        let answer = submitted_index.map(|index| &answers[index]);
        if answer.is_none_or(|answer| answer.1.is_empty()) {
            if question.2 {
                return refusal("required", vec![question_index]);
            }
            continue;
        }
        // The branch above proved both the index and answer are present.
        let index = submitted_index.unwrap();
        let answer = &answers[index];
        match question.1 {
            1 => match canonical_number(&answer.1, 5).filter(|&number| number >= 1) {
                Some(number) => rating = Some(number),
                None => return refusal("rating", vec![question_index]),
            },
            2 => match canonical_number(&answer.1, 10) {
                Some(number) => nps = Some(number),
                None => return refusal("nps", vec![question_index]),
            },
            3 => {
                if !question.3.contains(&answer.1) {
                    return refusal("choice", vec![question_index]);
                }
            }
            _ => {
                if answer.2 > 10_000 {
                    return refusal("length", vec![question_index]);
                }
            }
        }
        normalized.push((question_index, index));
    }
    (normalized, rating, nps, None)
}

#[pyfunction]
#[pyo3(signature = (*, questions, answers))]
fn validate_batch(
    py: Python<'_>,
    questions: Vec<Question>,
    answers: &Bound<'_, PyAny>,
) -> PyResult<Result> {
    let mut owned = Vec::new();
    let mut seen = BTreeSet::new();
    // Keep iterator consumption attached and stop at the first duplicate, just
    // as the original Python validator did. No Python references enter detach.
    for answer in answers.try_iter()? {
        let answer: Answer = answer?.extract()?;
        if !seen.insert(answer.0.clone()) {
            return Ok(refusal("duplicate", vec![owned.len()]));
        }
        owned.push(answer);
    }
    Ok(py.detach(move || validate(questions, owned)))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(validate_batch, module)?)?;
    Ok(())
}
