//! Opt-in native screenshot smoke harness. No fixture is loaded in ordinary use.
use crate::{
    model::ResultDocument,
    nav::Page,
    tour::{Tour, RESULTS, SETUP},
};
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
    /// One shot per page, then one per setup-tour step, then one per results-tour step.
    pub fn total() -> usize {
        Page::ALL.len() + SETUP.len() + RESULTS.len()
    }
    /// The page to show and the tour step to open (if any) for a screenshot index.
    fn plan(shot: usize) -> (Page, Option<(bool, usize)>) {
        let pages = Page::ALL.len();
        let results_from = pages + SETUP.len();
        let page = Page::ALL[shot.min(pages - 1)];
        let tour = if shot >= results_from {
            Some((true, (shot - results_from).min(RESULTS.len() - 1)))
        } else if shot >= pages {
            Some((false, (shot - pages).min(SETUP.len() - 1)))
        } else {
            None
        };
        (page, tour)
    }
    pub fn prepare(
        &mut self,
        ctx: &egui::Context,
        page: &mut Page,
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
        let (shown, step) = Self::plan(self.shot);
        *page = shown;
        if step.is_some() {
            tour.active = step;
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
            if self.shot == Self::total() {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shot_count_covers_every_page_and_both_tours() {
        // `scripts/desktop_smoke.py` expects this many screenshots: 19 pages, 9 setup-tour
        // steps and 8 results-tour steps.
        assert_eq!(Capture::total(), 36);
        assert_eq!(Page::ALL.len(), 19);
        assert_eq!(SETUP.len(), 9);
        assert_eq!(RESULTS.len(), 8);
        assert_eq!(
            Capture::total(),
            Page::ALL.len() + SETUP.len() + RESULTS.len()
        );
    }

    #[test]
    fn shots_walk_the_pages_then_the_setup_tour_then_the_results_tour() {
        let pages = Page::ALL.len();
        for (shot, page) in Page::ALL.into_iter().enumerate() {
            assert_eq!(Capture::plan(shot), (page, None));
        }
        let last = Page::ALL[pages - 1];
        assert_eq!(Capture::plan(pages), (last, Some((false, 0))));
        let results_from = pages + SETUP.len();
        assert_eq!(
            Capture::plan(results_from - 1),
            (last, Some((false, SETUP.len() - 1)))
        );
        assert_eq!(Capture::plan(results_from), (last, Some((true, 0))));
        assert_eq!(
            Capture::plan(Capture::total() - 1),
            (last, Some((true, RESULTS.len() - 1)))
        );
    }
}
