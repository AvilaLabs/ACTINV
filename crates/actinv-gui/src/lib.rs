//! Browser workbench. The native executable retains its own entry point.
//! Both surfaces use the same problem schema, result parser, plot helpers and page bodies.
#![cfg(target_arch = "wasm32")]

mod model;
mod nav;
mod views;
mod visuals;
mod web;
mod web_app;
