//! Browser workbench. The native executable retains its own entry point.
//! Both surfaces use the same problem schema, result parser, and plot helpers.
#![cfg(target_arch = "wasm32")]

mod model;
mod visuals;
mod web;
mod web_app;
