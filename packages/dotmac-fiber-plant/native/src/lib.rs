use pyo3::prelude::*;
use std::collections::{HashMap, HashSet, VecDeque};

#[pyfunction]
fn continuity(py: Python<'_>, edges: Vec<(String, String, String)>, starts: Vec<String>, targets: Vec<String>) -> (Vec<String>, Vec<String>) {
    py.detach(move || {
        let mut graph: HashMap<String, Vec<(String, String)>> = HashMap::new();
        for (left, right, id) in edges {
            graph.entry(left.clone()).or_default().push((right.clone(), id.clone()));
            graph.entry(right).or_default().push((left, id));
        }
        let targets: HashSet<_> = targets.into_iter().collect();
        let mut visited: HashSet<_> = starts.iter().cloned().collect();
        let mut steps: Vec<(String, Option<usize>, String)> = starts.into_iter().map(|node| (node, None, String::new())).collect();
        let mut queue: VecDeque<_> = (0..steps.len()).collect();
        while let Some(index) = queue.pop_front() {
            let node = steps[index].0.clone();
            if targets.contains(&node) {
                let mut strands = Vec::new();
                let mut splices = Vec::new();
                let mut current = index;
                loop {
                    let (node, parent, link) = &steps[current];
                    strands.push(node.clone());
                    match parent {
                        Some(parent) => { splices.push(link.clone()); current = *parent; }
                        None => break,
                    }
                }
                strands.reverse(); splices.reverse();
                return (strands, splices);
            }
            if let Some(edges) = graph.get(&node) {
                for (next, id) in edges {
                    if visited.insert(next.clone()) {
                        let next_index = steps.len();
                        steps.push((next.clone(), Some(index), id.clone()));
                        queue.push_back(next_index);
                    }
                }
            }
        }
        (Vec::new(), Vec::new())
    })
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(continuity, module)?)?;
    Ok(())
}
