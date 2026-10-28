use crate::views::import;
use actinv_core::report;
use serde_json::{json, Value};
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;

pub fn download(name: &str, bytes: &[u8]) -> Result<(), String> {
    let document = web_sys::window()
        .and_then(|window| window.document())
        .ok_or("Browser document is unavailable")?;
    let parts = js_sys::Array::new();
    parts.push(&js_sys::Uint8Array::from(bytes));
    let blob = web_sys::Blob::new_with_u8_array_sequence(&parts)
        .map_err(|_| "Could not prepare the download")?;
    let url = web_sys::Url::create_object_url_with_blob(&blob)
        .map_err(|_| "Could not create the download URL")?;
    let result = (|| {
        let anchor = document
            .create_element("a")
            .map_err(|_| "Could not create download link")?;
        anchor
            .set_attribute("href", &url)
            .map_err(|_| "Could not set download URL")?;
        anchor
            .set_attribute("download", name)
            .map_err(|_| "Could not set download filename")?;
        let element = anchor
            .dyn_into::<web_sys::HtmlElement>()
            .map_err(|_| "Could not open download link")?;
        let body = document.body().ok_or("Browser document has no body")?;
        body.append_child(&element)
            .map_err(|_| "Could not attach download link")?;
        element.click();
        element.remove();
        Ok(())
    })();
    let _ = web_sys::Url::revoke_object_url(&url);
    result
}

/// Mark the page while the problem has unsaved edits, so the tab-close guard in `index.html`
/// asks before the edits are lost.
pub fn mark_unsaved(unsaved: bool) {
    let Some(element) = web_sys::window()
        .and_then(|window| window.document())
        .and_then(|document| document.document_element())
    else {
        return;
    };
    if element.has_attribute("data-unsaved") != unsaved {
        let _ = if unsaved {
            element.set_attribute("data-unsaved", "")
        } else {
            element.remove_attribute("data-unsaved")
        };
    }
}

/// Test hook (`web/smoke.mjs`): convert a FISPACT-II deck exactly as the import dialog does.
/// Returns `{"spec", "report", "placeholder"}` as JSON text, or `{"error"}`. `fluxes` is the text
/// of a fluxes file; without it a decay-only deck converts and an irradiating one gets the
/// placeholder spectrum.
#[wasm_bindgen]
pub fn convert_deck(deck: &str, fluxes: Option<String>) -> String {
    let deck = import::Slot {
        name: "deck.i".into(),
        text: deck.into(),
    };
    let fluxes = fluxes.map(|text| import::Slot {
        name: "fluxes".into(),
        text,
    });
    match import::convert_deck(&deck, fluxes.as_ref(), None) {
        Ok(done) => {
            json!({"spec": done.spec, "report": done.report, "placeholder": done.placeholder})
                .to_string()
        }
        Err(error) => json!({"error": error}).to_string(),
    }
}

/// Test hook (`web/smoke.mjs`): one report table of a result as CSV, the text `actinv report
/// --format csv --tables <table>` writes. `step` is the result's own step number; 0 takes every
/// step. A failure comes back as `{"error"}` JSON, which no CSV can begin with.
#[wasm_bindgen]
pub fn report_csv(result_json: &str, table: &str, step: u32) -> String {
    let fail = |message: String| json!({"error": message}).to_string();
    let Some(kind) = report::TableKind::from_name(table) else {
        return fail(format!("unknown table '{table}'"));
    };
    let result: Value = match serde_json::from_str(result_json) {
        Ok(result) => result,
        Err(error) => return fail(format!("invalid result JSON: {error}")),
    };
    let request = report::Request {
        tables: vec![kind],
        step: if step == 0 {
            report::StepSelect::All
        } else {
            report::StepSelect::One(u64::from(step))
        },
        nuclides: Vec::new(),
    };
    match report::build(&result, &request) {
        Ok(outcomes) => match outcomes.into_iter().next().map(|outcome| outcome.built) {
            Some(Ok(built)) => report::render(&built, report::Format::Csv),
            Some(Err(reason)) => fail(reason),
            None => fail("no table was built".into()),
        },
        Err(error) => fail(error),
    }
}

/// Test hook (`web/smoke.mjs`): open a waste result exactly as the Waste class page does. Returns
/// `{"source", "components": [{"id", "steps": [{"step", "class"}]}]}` as JSON text, or `{"error"}`
/// with the message the page shows.
#[wasm_bindgen]
pub fn waste_view(text: &str) -> String {
    match crate::views::waste::read_result(text) {
        Ok(view) => view.classes().to_string(),
        Err(error) => json!({"error": error}).to_string(),
    }
}

#[wasm_bindgen(start)]
pub async fn start() -> Result<(), JsValue> {
    console_error_panic_hook::set_once();
    let document = web_sys::window()
        .and_then(|window| window.document())
        .ok_or_else(|| JsValue::from_str("Browser document is unavailable"))?;
    let canvas = document
        .get_element_by_id("actinv-canvas")
        .and_then(|element| element.dyn_into::<web_sys::HtmlCanvasElement>().ok())
        .ok_or_else(|| JsValue::from_str("ACTINV canvas is unavailable"))?;
    let result = eframe::WebRunner::new()
        .start(
            canvas,
            eframe::WebOptions {
                renderer: eframe::Renderer::Glow,
                ..Default::default()
            },
            Box::new(|cc| Ok(Box::new(crate::web_app::Workbench::new(cc)))),
        )
        .await;
    if let Some(loading) = document.get_element_by_id("loading") {
        match &result {
            Ok(()) => loading.remove(),
            Err(_) => loading.set_text_content(Some("ACTINV could not start. Enable WebGL and hardware acceleration, or use the desktop download above.")),
        }
    }
    result
}
