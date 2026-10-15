use num_bigint::BigInt;
use pyo3::prelude::*;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};

type Link = (String, String, String, bool, BigInt);
type Path = (Vec<String>, Vec<String>, BigInt);
struct Step {
    node: String,
    parent: Option<usize>,
    link: String,
}

fn resolve(
    graph: &HashMap<String, Vec<(String, String, BigInt)>>,
    source: String,
    destination: String,
) -> Path {
    let zero = BigInt::from(0);
    let mut steps = vec![Step {
        node: source.clone(),
        parent: None,
        link: String::new(),
    }];
    let mut queue = BinaryHeap::from([Reverse((zero.clone(), source.clone(), 0usize))]);
    let mut best = HashMap::from([(source, zero.clone())]);
    while let Some(Reverse((cost, node, index))) = queue.pop() {
        if best.get(&node) != Some(&cost) {
            continue;
        }
        if node == destination {
            let mut hops = Vec::new();
            let mut links = Vec::new();
            let mut current = index;
            loop {
                let step = &steps[current];
                hops.push(step.node.clone());
                match step.parent {
                    Some(parent) => {
                        links.push(step.link.clone());
                        current = parent;
                    }
                    None => break,
                }
            }
            hops.reverse();
            links.reverse();
            return (hops, links, cost);
        }
        if let Some(edges) = graph.get(&node) {
            for (next, link, edge_cost) in edges {
                let next_cost = &cost + edge_cost;
                if best.get(next).map_or(true, |old| next_cost < *old) {
                    best.insert(next.clone(), next_cost.clone());
                    let next_index = steps.len();
                    steps.push(Step {
                        node: next.clone(),
                        parent: Some(index),
                        link: link.clone(),
                    });
                    // Strict improvements mean two live equal-cost entries cannot share a
                    // node. Python's later hops/UUID heap tie fields never decide a pop.
                    queue.push(Reverse((next_cost, next.clone(), next_index)));
                }
            }
        }
    }
    (Vec::new(), Vec::new(), zero)
}

#[pyfunction]
fn resolve_paths(py: Python<'_>, links: Vec<Link>, queries: Vec<(String, String)>) -> Vec<Path> {
    py.detach(move || {
        let mut graph: HashMap<String, Vec<(String, String, BigInt)>> = HashMap::new();
        for (left, right, id, bidirectional, cost) in links {
            graph
                .entry(left.clone())
                .or_default()
                .push((right.clone(), id.clone(), cost.clone()));
            if bidirectional {
                graph.entry(right).or_default().push((left, id, cost));
            }
        }
        queries
            .into_iter()
            .map(|(source, target)| resolve(&graph, source, target))
            .collect()
    })
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(resolve_paths, module)?)?;
    Ok(())
}
