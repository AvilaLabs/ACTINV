//! Opt-in native screenshot smoke harness. No fixture is loaded in ordinary use.
use crate::{model::ResultDocument, tour::Tour};
use eframe::egui;
use std::path::PathBuf;

pub struct Capture {
    directory: PathBuf,
    frame: usize,
    shot: usize,
    waiting: bool,
    loaded: bool,
}
impl Capture {
    pub fn from_env() -> Option<Self> {
        std::env::var_os("ACTINV_GUI_CAPTURE_DIR").map(|p| Self {
            directory: p.into(),
            frame: 0,
            shot: 0,
            waiting: false,
            loaded: false,
        })
    }
    pub fn prepare(
        &mut self,
        ctx: &egui::Context,
        page: &mut usize,
        tour: &mut Tour,
        result: &mut Option<ResultDocument>,
    ) {
        if !self.loaded {
            self.loaded = true;
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_RESULT") {
                let text = std::fs::read_to_string(path).expect("capture result file");
                *result = Some(
                    ResultDocument::parse(
                        serde_json::from_str(&text).expect("capture JSON"),
                        "UI smoke fixture".into(),
                    )
                    .expect("capture result"),
                );
            }
        }
        *page = self.shot.min(7);
        if self.shot >= 15 {
            tour.active = Some((true, (self.shot - 15).min(5)));
        } else if self.shot >= 8 {
            tour.active = Some((false, (self.shot - 8).min(6)));
        }
        ctx.request_repaint();
    }
    pub fn finish(&mut self, ctx: &egui::Context) {
        let screenshot = ctx.input(|i| {
            i.events.iter().find_map(|e| {
                if let egui::Event::Screenshot { image, .. } = e {
                    Some(image.clone())
                } else {
                    None
                }
            })
        });
        if let Some(image) = screenshot {
            std::fs::create_dir_all(&self.directory).expect("capture directory");
            let bytes: Vec<u8> = image.pixels.iter().flat_map(|c| c.to_array()).collect();
            image::save_buffer(
                self.directory.join(format!("{:02}.png", self.shot)),
                &bytes,
                image.size[0] as u32,
                image.size[1] as u32,
                image::ColorType::Rgba8,
            )
            .expect("save screenshot");
            self.shot += 1;
            self.waiting = false;
            self.frame = 0;
            if self.shot == 21 {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }
        self.frame += 1;
        if self.frame == 8 && !self.waiting {
            self.waiting = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(Default::default()));
        }
    }
}
