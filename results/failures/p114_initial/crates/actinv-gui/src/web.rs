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
