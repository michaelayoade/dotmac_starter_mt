//! Bulk response validation over reversible Python Unicode bytes and indices.

use std::collections::{BTreeMap, BTreeSet};
use std::ffi::CString;

use dotmac_decimal_core::{valid_context, Context, ContextSpec, Decimal, Status};

use pyo3::exceptions::PyValueError;
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

const MEMORY: u32 = 1 << 9;
type ArithmeticFailure = Option<(u32, u32)>;
type ArithmeticResult = (Option<String>, u32, ArithmeticFailure);

#[derive(Default)]
struct Signals {
    raised: u32,
    failed: ArithmeticFailure,
}

impl Signals {
    fn record(&mut self, status: Status, traps: u32) -> bool {
        self.raised |= status.signals;
        if status.signals & (traps | MEMORY) != 0 {
            self.failed = Some((status.signals, status.conditions));
            return false;
        }
        true
    }

    fn finish(self, value: Option<String>) -> ArithmeticResult {
        (value, self.raised, self.failed)
    }
}

fn memory_failure() -> ArithmeticResult {
    (None, MEMORY, None)
}

// The domain operation order is sequential sum, division, quantization. The
// shared core supplies quiet arithmetic; no integer or floating point shortcut
// can replace these operations under low-precision ambient Decimal contexts.
fn rating_average(ratings: Vec<CString>, spec: ContextSpec, traps: u32) -> ArithmeticResult {
    if ratings.is_empty() {
        return (None, 0, None);
    }
    let Some(context) = Context::new(spec) else {
        return memory_failure();
    };
    let (Some(mut total), Some(mut operand), Some(mut scale)) =
        (Decimal::new(), Decimal::new(), Decimal::new())
    else {
        return memory_failure();
    };
    let mut status = Signals::default();
    if !status.record(total.parse(c"0"), traps)
        || !status.record(scale.parse(c"0.01"), traps)
    {
        return status.finish(None);
    }
    for text in &ratings {
        if !status.record(operand.parse(text), traps)
            || !status.record(total.add(&operand, &context), traps)
        {
            return status.finish(None);
        }
    }
    let count = CString::new(ratings.len().to_string()).expect("decimal count has no NUL");
    if !status.record(operand.parse(&count), traps)
        || !status.record(total.div(&operand, &context), traps)
        || !status.record(total.quantize(&scale, &context, None), traps)
    {
        return status.finish(None);
    }
    let result = total.text();
    if result.is_none() {
        status.raised |= MEMORY;
    }
    status.finish(result)
}

fn nps_score(promoters: usize, detractors: usize, count: usize, spec: ContextSpec, traps: u32) -> ArithmeticResult {
    if count == 0 {
        return (None, 0, None);
    }
    let Some(context) = Context::new(spec) else {
        return memory_failure();
    };
    let (Some(mut value), Some(mut operand), Some(mut scale)) =
        (Decimal::new(), Decimal::new(), Decimal::new())
    else {
        return memory_failure();
    };
    let difference = if promoters >= detractors {
        (promoters - detractors).to_string()
    } else {
        format!("-{}", detractors - promoters)
    };
    let difference = CString::new(difference).expect("decimal count has no NUL");
    let count = CString::new(count.to_string()).expect("decimal count has no NUL");
    let mut status = Signals::default();
    if !status.record(value.parse(&difference), traps)
        || !status.record(operand.parse(&count), traps)
        || !status.record(value.div(&operand, &context), traps)
        || !status.record(operand.parse(c"100"), traps)
        || !status.record(value.mul(&operand, &context), traps)
        || !status.record(scale.parse(c"0.01"), traps)
        || !status.record(value.quantize(&scale, &context, None), traps)
    {
        return status.finish(None);
    }
    let result = value.text();
    if result.is_none() {
        status.raised |= MEMORY;
    }
    status.finish(result)
}

#[pyfunction]
#[pyo3(signature = (*, ratings, nps_predicates, context, traps, replay_rating, capture_nps_context))]
fn calculate_metrics(
    py: Python<'_>,
    ratings: Vec<String>,
    nps_predicates: &Bound<'_, PyAny>,
    context: ContextSpec,
    traps: u32,
    replay_rating: &Bound<'_, PyAny>,
    capture_nps_context: &Bound<'_, PyAny>,
) -> PyResult<(Option<String>, Option<String>, u32, ArithmeticFailure)> {
    if !valid_context(context) || traps & !0x1ff != 0 {
        return Err(PyValueError::new_err("invalid Decimal batch context"));
    }
    let ratings: Vec<CString> = ratings.into_iter().map(CString::new)
        .collect::<std::result::Result<_, _>>()
        .map_err(|_| PyValueError::new_err("Decimal text contains NUL"))?;
    let (average, raised, failure) = py.detach(move || rating_average(ratings, context, traps));
    // Replaying once here ensures rating traps and flags precede every NPS
    // rich comparison, including a comparison that raises a Python exception.
    replay_rating.call1((raised, failure))?;
    let mut promoters = 0;
    let mut detractors = 0;
    let mut count = 0;
    for predicate in nps_predicates.try_iter()? {
        let (promoter_pass, included): (bool, bool) = predicate?.extract()?;
        if promoter_pass {
            count += 1;
            promoters += usize::from(included);
        } else {
            detractors += usize::from(included);
        }
    }
    // NPS rich comparisons may change the current Decimal context or traps.
    // Capture once after the complete predicate stream, not once per operation.
    let (nps_context, nps_traps): (ContextSpec, u32) =
        capture_nps_context.call0()?.extract()?;
    if !valid_context(nps_context) || nps_traps & !0x1ff != 0 {
        return Err(PyValueError::new_err("invalid Decimal NPS context"));
    }
    let (score, raised, failure) = py.detach(move || {
        nps_score(promoters, detractors, count, nps_context, nps_traps)
    });
    Ok((average, score, raised, failure))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(validate_batch, module)?)?;
    module.add_function(wrap_pyfunction!(calculate_metrics, module)?)?;
    Ok(())
}
