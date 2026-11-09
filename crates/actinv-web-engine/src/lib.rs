//! The browser's engine: a web worker that runs a problem from a library pack
//! (`actinv_core::pack`), so the page stays responsive while it solves.
//!
//! Once started the worker posts `{id: 0, kind: "ready"}`; until then a message is lost. The page
//! then posts `{id, spec, pack, reactions, inputs}`: the problem's JSON text, the pack's base
//! URL, a fixed number of reactions to follow (absent: deepen round by round until the result
//! settles, `pack::Deepening`) and an array of `[path, Uint8Array]` pairs for every other file the
//! problem names. The worker fetches the pack's graph and the files each round's plan names,
//! keeping both for later runs, and answers with messages carrying the same `id`: `{kind:
//! "progress", round, done, total}` while fetching, `{kind: "solving", round}`, then `{kind:
//! "result", result}` (the result JSON text) or `{kind: "error", message}`. `round` counts from 0;
//! a later round checks the earlier one with more reactions. A run cannot be interrupted; the page
//! cancels by terminating the worker.
//!
//! The page starts a worker per run, so the files also go into the browser's cache (Cache
//! Storage), under [`cache_name`] and [`cache_key`]: a later run, or a later visit, reads them from
//! there instead of the network. A cached file is used only if its bytes have the SHA-256 the graph
//! records, and is otherwise fetched again. Where the browser has no cache for the page (outside a
//! secure context, or with site storage blocked), every file comes from the network.

use actinv_core::pack::PackGraph;

/// Files fetched at once.
pub const CONCURRENT_FETCHES: usize = 24;

/// A pack file's URL under the pack's base URL.
pub fn file_url(base: &str, file: &str) -> String {
    format!("{}/{file}", base.trim_end_matches('/'))
}

/// What every pack cache's name starts with.
pub const CACHE_PREFIX: &str = "actinv-pack-";

/// The cache holding a pack's files, named by the data the pack was built from. A run deletes the
/// caches of other packs, so a visitor keeps the files of one pack at most.
pub fn cache_name(graph: &PackGraph) -> String {
    let mut name = format!("{CACHE_PREFIX}{}", short(&graph.library_sha256));
    for source in &graph.decay {
        name.push('-');
        name.push_str(short(&source.sha256));
    }
    name
}

fn short(sha256: &str) -> &str {
    &sha256[..sha256.len().min(16)]
}

/// A cached file's key: its URL with the SHA-256 the graph records, so a file rebuilt at the same
/// URL is not found.
pub fn cache_key(base: &str, file: &str, sha256: &str) -> String {
    format!("{}?sha256={sha256}", file_url(base, file))
}

#[cfg(target_arch = "wasm32")]
mod worker {
    use super::*;
    use actinv_core::pack;
    use actinv_core::spec::Spec;
    use std::cell::RefCell;
    use std::collections::HashMap;
    use std::collections::HashSet;
    use std::rc::Rc;
    use wasm_bindgen::prelude::*;
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;
    use web_sys::{
        Cache, CacheStorage, DedicatedWorkerGlobalScope, MessageEvent, Request, Response, Url,
    };

    thread_local! {
        /// Graphs by pack URL, and fetched files by URL, kept for later runs.
        static GRAPHS: RefCell<HashMap<String, Rc<PackGraph>>> = RefCell::new(HashMap::new());
        static FILES: RefCell<HashMap<String, Vec<u8>>> = RefCell::new(HashMap::new());
        /// Cache writes not yet finished. Fetching goes on without them; a run waits for them
        /// before it answers, since the page then terminates the worker.
        static WRITES: RefCell<Vec<js_sys::Promise>> = const { RefCell::new(Vec::new()) };
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

    /// A pack's cache and the keys it held when the run opened it, as absolute URLs: a file whose
    /// key is not among them is fetched without asking the cache, which costs a first visit a lookup
    /// per file otherwise.
    struct Kept {
        cache: Cache,
        keys: HashSet<String>,
    }

    fn absolute(url: &str) -> String {
        Url::new_with_base(url, &scope().location().href())
            .map(|url| url.href())
            .unwrap_or_else(|_| url.to_owned())
    }

    /// The browser's cache for this pack, after deleting other packs' caches; `None` where the
    /// browser has none for this page or refuses to open it.
    async fn open_cache(graph: &PackGraph) -> Option<Rc<Kept>> {
        // `caches` is undefined outside a secure context.
        let storage = js_sys::Reflect::get(&scope(), &"caches".into()).ok()?;
        let storage: CacheStorage = storage.dyn_into().ok()?;
        let name = cache_name(graph);
        if let Ok(names) = JsFuture::from(storage.keys()).await {
            for old in js_sys::Array::from(&names)
                .iter()
                .filter_map(|n| n.as_string())
            {
                if old.starts_with(CACHE_PREFIX) && old != name {
                    let _ = JsFuture::from(storage.delete(&old)).await;
                }
            }
        }
        let cache: Cache = JsFuture::from(storage.open(&name))
            .await
            .ok()?
            .dyn_into()
            .ok()?;
        let keys = match JsFuture::from(cache.keys()).await {
            Ok(keys) => js_sys::Array::from(&keys)
                .iter()
                .filter_map(|key| key.dyn_into::<Request>().ok().map(|key| key.url()))
                .collect(),
            Err(_) => HashSet::new(),
        };
        Some(Rc::new(Kept { cache, keys }))
    }

    /// One file's bytes: from the cache if they have the graph's SHA-256 there, otherwise from the
    /// network, then cached if they have it. A failing cache is passed over.
    async fn load(
        kept: Option<Rc<Kept>>,
        url: String,
        key: String,
        sha256: Option<String>,
    ) -> Result<Vec<u8>, String> {
        let kept = kept.zip(sha256);
        if let Some((kept, sha256)) = kept.as_ref().filter(|(kept, _)| kept.keys.contains(&key)) {
            if let Ok(hit) = JsFuture::from(kept.cache.match_with_str(&key)).await {
                if let Ok(response) = hit.dyn_into::<Response>() {
                    if let Ok(bytes) = body(response, &url).await {
                        if pack::sha256_hex(&bytes) == *sha256 {
                            return Ok(bytes);
                        }
                    }
                }
            }
        }
        let bytes = body(fetch_response(&url).await?, &url).await?;
        if let Some((kept, sha256)) = &kept {
            if pack::sha256_hex(&bytes) == *sha256 {
                let copy = js_sys::Uint8Array::from(bytes.as_slice());
                if let Ok(response) = Response::new_with_opt_buffer_source(Some(&copy)) {
                    let write = kept.cache.put_with_str(&key, &response);
                    WRITES.with(|writes| writes.borrow_mut().push(write));
                }
            }
        }
        Ok(bytes)
    }

    /// Load every file not yet held, `CONCURRENT_FETCHES` at a time, reporting progress.
    async fn fetch_all(
        id: f64,
        round: usize,
        base: &str,
        files: &[String],
        hashes: &HashMap<&str, &str>,
        kept: &Option<Rc<Kept>>,
    ) -> Result<(), String> {
        let missing: Vec<&String> = files
            .iter()
            .filter(|file| !FILES.with(|held| held.borrow().contains_key(&file_url(base, file))))
            .collect();
        let total = missing.len();
        post(
            id,
            &[
                ("kind", "progress".into()),
                ("round", round.into()),
                ("done", 0.into()),
                ("total", total.into()),
            ],
        );
        for (batch, chunk) in missing.chunks(CONCURRENT_FETCHES).enumerate() {
            // Every load in the chunk starts before the first is awaited.
            let started: Vec<js_sys::Promise> = chunk
                .iter()
                .map(|file| {
                    let url = file_url(base, file);
                    let sha256 = hashes.get(file.as_str()).map(|sha| sha.to_string());
                    let key = absolute(&cache_key(
                        base,
                        file,
                        sha256.as_deref().unwrap_or_default(),
                    ));
                    let loading = load(kept.clone(), url.clone(), key, sha256);
                    wasm_bindgen_futures::future_to_promise(async move {
                        let bytes = loading.await?;
                        FILES.with(|held| held.borrow_mut().insert(url, bytes));
                        Ok(JsValue::UNDEFINED)
                    })
                })
                .collect();
            for promise in started {
                JsFuture::from(promise).await.map_err(described)?;
            }
            let done = (batch * CONCURRENT_FETCHES + chunk.len()).min(total);
            post(
                id,
                &[
                    ("kind", "progress".into()),
                    ("round", round.into()),
                    ("done", done.into()),
                    ("total", total.into()),
                ],
            );
        }
        Ok(())
    }

    /// Wait for the cache writes so far. A failed one (a full cache, blocked storage) only costs a
    /// later run a fetch.
    async fn written() {
        for write in WRITES.with(|writes| writes.take()) {
            let _ = JsFuture::from(write).await;
        }
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
            let fixed = field(&data, "reactions")
                .as_f64()
                .map(|value| value as usize);
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
            let hashes = graph.file_hashes();
            let kept = open_cache(&graph).await;
            let problem = Spec::from_json(&spec)?;
            let mut deepening = pack::Deepening::new();
            let mut reactions = fixed.unwrap_or(pack::ROUNDS[0]);
            let mut round = 0;
            let result = loop {
                let (reach, files) = pack::plan(&problem, &graph, reactions)?;
                fetch_all(id, round, &base, &files, &hashes, &kept).await?;
                post(id, &[("kind", "solving".into()), ("round", round.into())]);
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
                let result = pack::run_from_pack(
                    &spec,
                    "browser",
                    &graph,
                    reactions,
                    &pack_files,
                    inputs.clone(),
                )?;
                if fixed.is_some() {
                    break result;
                }
                match deepening.solved(&reach, result) {
                    pack::Next::Run(deeper) => {
                        reactions = deeper;
                        round += 1;
                    }
                    pack::Next::Done(result) => break *result,
                }
            };
            serde_json::to_string(&result).map_err(|e| e.to_string())
        }
        .await;
        written().await;
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
    use super::{cache_key, cache_name, file_url, CACHE_PREFIX};
    use actinv_core::pack::{PackDecaySource, PackGraph};

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

    fn graph(library: &str, decay: &[&str]) -> PackGraph {
        PackGraph {
            schema: actinv_core::pack::SCHEMA.into(),
            library_sha256: library.into(),
            index_sha256: "0".repeat(64),
            index: serde_json::Value::Null,
            targets: Vec::new(),
            decay: decay
                .iter()
                .map(|sha256| PackDecaySource {
                    sha256: (*sha256).into(),
                    states: Vec::new(),
                })
                .collect(),
            names: Default::default(),
            daughters: Vec::new(),
        }
    }

    #[test]
    fn a_cache_is_named_by_the_data_its_pack_was_built_from() {
        let (a, b, c) = ("a".repeat(64), "b".repeat(64), "c".repeat(64));
        let name = cache_name(&graph(&a, &[&b, &c]));
        assert_eq!(
            name,
            format!("{CACHE_PREFIX}{}-{}-{}", &a[..16], &b[..16], &c[..16])
        );
        assert_ne!(name, cache_name(&graph(&a, &[&c, &b])));
        assert_ne!(name, cache_name(&graph(&b, &[&b, &c])));
    }

    #[test]
    fn a_cache_key_carries_the_files_hash() {
        assert_eq!(
            cache_key("./data/tendl/", "targets/26056_0.npz", "ab12"),
            "./data/tendl/targets/26056_0.npz?sha256=ab12"
        );
        assert_ne!(
            cache_key("./p", "decay/0/1_0.txt", "ab12"),
            cache_key("./p", "decay/0/1_0.txt", "cd34")
        );
    }
}
