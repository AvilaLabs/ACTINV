//! Browser workbench. The native executable retains its own entry point.
//! Both surfaces use the same problem schema, result parser, and plot helpers.
#![cfg(target_arch = "wasm32")]

mod model;
// The page bodies are shared with the desktop shell; 0.4 phase 5 wires them into the browser.
#[allow(dead_code)]
mod views;
mod visuals;
mod web;
mod web_app;
