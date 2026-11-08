//! The browser's engine: a web worker that runs a problem from a library pack
//! (`actinv_core::pack`), so the page stays responsive while it solves.
//!
//! Once started the worker posts `{id: 0, kind: "ready"}`; until then a message is lost. The page
//! then posts `{id, spec, pack, reactions, inputs}`: the problem's JSON text, the pack's base
//! URL, the reactions to follow (absent: the pack default) and an array of `[path, Uint8Array]`
//! pairs for every other file the problem names. The worker fetches the pack's graph and the
//! files the plan names, keeping both for later runs, and answers with messages carrying the same
//! `id`: `{kind: "progress", done, total}` while fetching, `{kind: "solving"}`, then
//! `{kind: "result", result}` (the result JSON text) or `{kind: "error", message}`. A run cannot
//! be interrupted; the page cancels by terminating the worker.

use actinv_core::pack::{self, PackGraph};
use std::collections::HashMap;

/// Files fetched at once.
pub const CONCURRENT_FETCHES: usize = 24;

/// A pack file's URL under the pack's base URL.
pub fn file_url(base: &str, file: &str) -> String {
    format!("{}/{file}", base.trim_end_matches('/'))
}

/// Every pack file a run of `spec_text` needs, by pack path.
pub fn wanted(spec_text: &str, graph: &PackGraph, reactions: usize) -> Result<Vec<String>, String> {
    let spec = actinv_core::spec::Spec::from_json(spec_text)?;
    let (_, files) = pack::plan(&spec, graph, reactions)?;
    Ok(files)
}

/// Run `spec_text` from the fetched pack files (by pack path) and the page's own inputs.
pub fn solve(
    spec_text: &str,
    graph: &PackGraph,
    reactions: usize,
    pack_files: &HashMap<String, Vec<u8>>,
    inputs: HashMap<String, Vec<u8>>,
) -> Result<String, String> {
    let result = pack::run_from_pack(spec_text, "browser", graph, reactions, pack_files, inputs)?;
    serde_json::to_string(&result).map_err(|e| e.to_string())
}

#[cfg(target_arch = "wasm32")]
mod worker {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;
    use wasm_bindgen::prelude::*;
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;
    use web_sys::{DedicatedWorkerGlobalScope, MessageEvent, Response};

    thread_local! {
        /// Graphs by pack URL, and fetched files by URL, kept for later runs.
        static GRAPHS: RefCell<HashMap<String, Rc<PackGraph>>> = RefCell::new(HashMap::new());
        static FILES: RefCell<HashMap<String, Vec<u8>>> = RefCell::new(HashMap::new());
    }

    fn scope() -> DedicatedWorkerGlobalScope {
        js_sys::global().unchecked_into()
    }

    fn post(id: f64, fields: &[(&str, JsValue)]) {
        let message = js_sys::Object::new();
        let _ = js_sys::Reflect::set(&message, &"id".into(), &id.into());
        for (key, value) in fields {
            let _ = js_sys::Reflect::set(&message, &(*key).into(), value);
        }
        let _ = scope().post_message(&message);
    }

    fn field(data: &JsValue, name: &str) -> JsValue {
        js_sys::Reflect::get(data, &name.into()).unwrap_or(JsValue::UNDEFINED)
    }

    fn described(error: JsValue) -> String {
        error
            .as_string()
            .or_else(|| {
                error
                    .dyn_ref::<js_sys::Error>()
                    .map(|error| String::from(error.message()))
            })
            .unwrap_or_else(|| "the browser refused the request".into())
    }

    async fn fetch_response(url: &str) -> Result<Response, String> {
        let response = JsFuture::from(scope().fetch_with_str(url))
            .await
            .map_err(|error| format!("{url}: {}", described(error)))?;
        let response: Response = response.unchecked_into();
        if !response.ok() {
            return Err(format!("{url}: HTTP {}", response.status()));
        }
        Ok(response)
    }

    async fn body(response: Response, url: &str) -> Result<Vec<u8>, String> {
        let buffer = response
            .array_buffer()
            .map_err(|error| format!("{url}: {}", described(error)))?;
        let buffer = JsFuture::from(buffer)
            .await
            .map_err(|error| format!("{url}: {}", described(error)))?;
        Ok(js_sys::Uint8Array::new(&buffer).to_vec())
    }

    async fn graph(base: &str) -> Result<Rc<PackGraph>, String> {
        if let Some(graph) = GRAPHS.with(|graphs| graphs.borrow().get(base).cloned()) {
            return Ok(graph);
        }
        let url = file_url(base, "graph.json");
        let response = fetch_response(&url).await.map_err(|error| {
            format!("The nuclear data for browser runs are not available here ({error}).")
        })?;
        let bytes = body(response, &url).await?;
        let graph: PackGraph = serde_json::from_slice(&bytes).map_err(|e| format!("{url}: {e}"))?;
        if graph.schema != pack::SCHEMA {
            return Err(format!(
                "{url} is a {} pack, not {}",
                graph.schema,
                pack::SCHEMA
            ));
        }
        let graph = Rc::new(graph);
        GRAPHS.with(|graphs| graphs.borrow_mut().insert(base.to_owned(), graph.clone()));
        Ok(graph)
    }

    /// Fetch every file not yet held, `CONCURRENT_FETCHES` at a time, reporting progress.
    async fn fetch_all(id: f64, base: &str, files: &[String]) -> Result<(), String> {
        let missing: Vec<&String> = files
            .iter()
            .filter(|file| !FILES.with(|held| held.borrow().contains_key(&file_url(base, file))))
            .collect();
        let total = missing.len();
        post(
            id,
            &[
                ("kind", "progress".into()),
                ("done", 0.into()),
                ("total", total.into()),
            ],
        );
        for (batch, chunk) in missing.chunks(CONCURRENT_FETCHES).enumerate() {
            // Every request in the chunk starts before the first is awaited.
            let started: Vec<(String, js_sys::Promise)> = chunk
                .iter()
                .map(|file| {
                    let url = file_url(base, file);
                    let promise = scope().fetch_with_str(&url);
                    (url, promise)
                })
                .collect();
            for (url, promise) in started {
                let response: Response = JsFuture::from(promise)
                    .await
                    .map_err(|error| format!("{url}: {}", described(error)))?
                    .unchecked_into();
                if !response.ok() {
                    return Err(format!("{url}: HTTP {}", response.status()));
                }
                let bytes = body(response, &url).await?;
                FILES.with(|held| held.borrow_mut().insert(url, bytes));
            }
            let done = (batch * CONCURRENT_FETCHES + chunk.len()).min(total);
            post(
                id,
                &[
                    ("kind", "progress".into()),
                    ("done", done.into()),
                    ("total", total.into()),
                ],
            );
        }
        Ok(())
    }

    async fn handle(data: JsValue) {
        let id = field(&data, "id").as_f64().unwrap_or(0.0);
        let result = async {
            let spec = field(&data, "spec")
                .as_string()
                .ok_or("the message has no spec")?;
            let base = field(&data, "pack")
                .as_string()
                .ok_or("the message has no pack")?;
            let reactions = field(&data, "reactions")
                .as_f64()
                .map(|value| value as usize)
                .unwrap_or(pack::DEFAULT_REACTIONS);
            let mut inputs = HashMap::new();
            let given = field(&data, "inputs");
            if !given.is_undefined() && !given.is_null() {
                for pair in js_sys::Array::from(&given).iter() {
                    let pair = js_sys::Array::from(&pair);
                    let path = pair.get(0).as_string().ok_or("an input has no path")?;
                    inputs.insert(path, js_sys::Uint8Array::new(&pair.get(1)).to_vec());
                }
            }
            let graph = graph(&base).await?;
            let files = wanted(&spec, &graph, reactions)?;
            fetch_all(id, &base, &files).await?;
            post(id, &[("kind", "solving".into())]);
            let pack_files: HashMap<String, Vec<u8>> = FILES.with(|held| {
                let held = held.borrow();
                files
                    .iter()
                    .filter_map(|file| {
                        held.get(&file_url(&base, file))
                            .map(|bytes| (file.clone(), bytes.clone()))
                    })
                    .collect()
            });
            solve(&spec, &graph, reactions, &pack_files, inputs)
        }
        .await;
        match result {
            Ok(text) => post(id, &[("kind", "result".into()), ("result", text.into())]),
            Err(message) => post(id, &[("kind", "error".into()), ("message", message.into())]),
        }
    }

    #[wasm_bindgen(start)]
    pub fn start() {
        let handler = Closure::<dyn FnMut(MessageEvent)>::new(|event: MessageEvent| {
            wasm_bindgen_futures::spawn_local(handle(event.data()));
        });
        scope().set_onmessage(Some(handler.as_ref().unchecked_ref()));
        handler.forget();
        // A message posted before this handler existed would be lost, so the page waits for this.
        post(0.0, &[("kind", "ready".into())]);
    }
}

#[cfg(test)]
mod tests {
    use super::file_url;

    #[test]
    fn file_urls_join_the_base_with_one_slash() {
        assert_eq!(
            file_url("./data/tendl/", "graph.json"),
            "./data/tendl/graph.json"
        );
        assert_eq!(
            file_url("https://x.org/p", "targets/26056_0.npz"),
            "https://x.org/p/targets/26056_0.npz"
        );
    }
}
