use crate::nav::Page;
use eframe::egui::{self, Color32, Id, Order, Rect, Stroke, StrokeKind, Vec2};
use std::collections::BTreeMap;

pub struct TourStep {
    pub target: &'static str,
    pub page: Page,
    pub title: &'static str,
    pub text: &'static str,
}
pub const SETUP: &[TourStep] = &[
    TourStep { target:"open",page:Page::Overview,title:"1. Open a calculation",text:"Click Open problem to load an ACTINV JSON file. Or keep the bundled iron example as your starting point. Next lets you explore without opening a file." },
    TourStep { target:"import",page:Page::Overview,title:"2. Import a FISPACT-II deck",text:"Have a FISPACT-II deck? Import deck converts it into an ACTINV problem and shows a keyword report: what was mapped, approximated, noted or left unsupported, and which inputs you still need to add." },
    TourStep { target:"inputs",page:Page::Overview,title:"3. Locate your nuclear data",text:"Choose the activation library and decay data. Relative paths use the input base folder shown here. ACTINV checks files before running; hashes remain part of the solver certificate." },
    TourStep { target:"material",page:Page::Material,title:"4. Define the material",text:"Edit the mass, composition basis, optional density, and isotope amounts. Add an element or isotope using its symbol, such as FE or Fe56. Validation checks the composition before a solve." },
    TourStep { target:"schedule",page:Page::Schedule,title:"5. Set irradiation and cooling",text:"Each row is a duration and what drives it: a flux multiplier, a wall loading or a power density. A flux of zero means cooling. Drag rows by their handle to reorder them, or use the arrow buttons. The timeline previews the sequence." },
    TourStep { target:"spectrum",page:Page::Spectrum,title:"6. Inspect the incident spectrum",text:"The plot shows the supplied group values. Edit the JSON for custom energy boundaries or advanced settings; a wall-loading reference can be set here. Spectrum normalization is always explicit." },
    TourStep { target:"outputs",page:Page::Outputs,title:"7. Choose what to compute",text:"Switch on the step summary, decay photons and dose, production pathways, regulatory tables, self-shielding or uncertainty. Inventory, activity and decay heat are always included. Conflicts between choices are reported here." },
    TourStep { target:"validate",page:Page::Outputs,title:"8. Validate your inputs",text:"Click Validate to check the scientific specification and file locations. Any error appears in the status area with an explanation. Validation does not run the solver." },
    TourStep { target:"run",page:Page::Outputs,title:"9. Run the calculation",text:"Click Run when your data are ready. The solver runs in the background. The result opens when it completes; you can then inspect charts, tables, photons, pathways, and the ledger." },
];
pub const RESULTS: &[TourStep] = &[
    TourStep {target:"result_open",page:Page::Charts,title:"1. Open or compare results",text:"Open an existing ACTINV result JSON, or run a calculation. Add a comparison result to overlay another calculation, and pick the computed step with the slider. No demonstration values are substituted for your results."},
    TourStep {target:"chart",page:Page::Charts,title:"2. Explore time histories",text:"Choose activity, total decay heat, or inventory. Drag to pan and scroll to zoom. Click a time on the chart to select the nearest computed step. Reset view restores the bounds."},
    TourStep {target:"tables",page:Page::Tables,title:"3. Read the tables",text:"Switch between the inventory, step totals, dominant nuclides, elements and the other report tables, filter them, and export a table as CSV. Tables the problem did not request are explained, with a button to enable them."},
    TourStep {target:"photons",page:Page::Photons,title:"4. Inspect photons and dose",text:"Decay photon groups, dose rates and any bremsstrahlung for the selected step, with dose against cooling time. Missing optional outputs are explained here; request them on the Outputs page before rerunning."},
    TourStep {target:"pathways",page:Page::Pathways,title:"5. Trace production pathways",text:"See the first products that feed a nuclide, and the full routes with how much of the target they explain. Missing optional outputs are explained here; request them on the Outputs page before rerunning."},
    TourStep {target:"ledger",page:Page::Ledger,title:"6. Read the evidence",text:"Expand ledger entries to inspect unaccounted data and approximations. The certificate records the solver's input provenance. These belong alongside the plotted answer."},
    TourStep {target:"export",page:Page::Charts,title:"7. Export the result",text:"Save the complete original result as JSON, or export the selected inventory as CSV. The JSON retains all steps, optional responses, the ledger, and the certificate."},
];
#[derive(Default)]
pub struct Tour {
    pub active: Option<(bool, usize)>,
    pub anchors: BTreeMap<&'static str, Rect>,
}
impl Tour {
    pub fn start(&mut self, results: bool) {
        self.active = Some((results, 0));
    }
    pub fn step(&self) -> Option<&'static TourStep> {
        self.active
            .map(|(r, i)| &if r { RESULTS } else { SETUP }[i])
    }
    pub fn mark(&mut self, name: &'static str, rect: Rect) {
        self.anchors.insert(name, rect);
    }
    pub fn show(&mut self, ctx: &egui::Context) {
        let Some((results, index)) = self.active else {
            return;
        };
        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            self.active = None;
            return;
        }
        let steps = if results { RESULTS } else { SETUP };
        let step = &steps[index];
        let screen = ctx.content_rect();
        let hole = self
            .anchors
            .get(step.target)
            .copied()
            .unwrap_or(Rect::from_center_size(screen.center(), Vec2::splat(40.)))
            .expand(5.)
            .intersect(screen);
        let rects = [
            Rect::from_min_max(screen.min, egui::pos2(screen.max.x, hole.min.y)),
            Rect::from_min_max(egui::pos2(screen.min.x, hole.max.y), screen.max),
            Rect::from_min_max(egui::pos2(screen.min.x, hole.min.y), hole.left_bottom()),
            Rect::from_min_max(hole.right_top(), egui::pos2(screen.max.x, hole.max.y)),
        ];
        for (i, rect) in rects.into_iter().enumerate() {
            egui::Area::new(Id::new(("tour-shade", i)))
                .order(Order::Foreground)
                .fade_in(false)
                .fixed_pos(rect.min)
                .show(ctx, |ui| {
                    let (r, _) = ui.allocate_exact_size(
                        rect.size().max(Vec2::ZERO),
                        egui::Sense::click_and_drag(),
                    );
                    ui.painter()
                        .rect_filled(r, 0., Color32::from_black_alpha(155));
                });
        }
        ctx.layer_painter(egui::LayerId::new(Order::Foreground, Id::new("tour-card")))
            .rect_stroke(
                hole,
                5.,
                Stroke::new(3., Color32::from_rgb(150, 170, 255)),
                StrokeKind::Outside,
            );
        let y = if hole.bottom() + 210. < screen.bottom() {
            hole.bottom() + 14.
        } else {
            (hole.top() - 210.).max(12.)
        };
        let x = hole.left().clamp(12., (screen.right() - 402.).max(12.));
        ctx.move_to_top(egui::LayerId::new(Order::Foreground, Id::new("tour-card")));
        egui::Area::new(Id::new("tour-card"))
            .order(Order::Foreground)
            .fade_in(false)
            .fixed_pos(egui::pos2(x, y))
            .show(ctx, |ui| {
                egui::Frame::popup(ui.style())
                    .fill(ui.visuals().window_fill())
                    .inner_margin(18.)
                    .show(ui, |ui| {
                        ui.set_width(350.);
                        ui.label(
                            egui::RichText::new("GUIDED WALKTHROUGH")
                                .small()
                                .color(Color32::from_rgb(24, 0, 173)),
                        );
                        ui.heading(step.title);
                        ui.add_space(8.);
                        ui.label(step.text);
                        ui.add_space(12.);
                        ui.horizontal(|ui| {
                            if ui.button("Exit tour").clicked() {
                                self.active = None;
                            }
                            if ui
                                .add_enabled(index > 0, egui::Button::new("Back"))
                                .clicked()
                            {
                                self.active = Some((results, index - 1));
                            }
                            let next = ui.button(if index + 1 == steps.len() {
                                "Finish"
                            } else {
                                "Next >"
                            });
                            self.anchors.insert("tour_next", next.rect);
                            if next.clicked() {
                                self.active = if index + 1 == steps.len() {
                                    None
                                } else {
                                    Some((results, index + 1))
                                };
                            }
                            ui.label(format!("{} / {}", index + 1, steps.len()));
                        });
                    });
            });
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tours_have_valid_targets_and_pages() {
        for steps in [SETUP, RESULTS] {
            assert!(!steps.is_empty());
            for step in steps {
                assert!(Page::ALL.contains(&step.page));
                assert_ne!(step.page, Page::Advanced);
                assert_ne!(step.page.group(), crate::nav::Group::Tools);
                assert!(!step.target.is_empty());
                assert!(!step.text.is_empty());
            }
        }
        let mut tour = Tour::default();
        tour.start(true);
        assert_eq!(tour.step().unwrap().target, "result_open");
    }
    #[test]
    fn setup_tour_places_import_after_open_and_outputs_before_validate() {
        let targets: Vec<_> = SETUP.iter().map(|s| s.target).collect();
        assert_eq!(&targets[..2], ["open", "import"]);
        let outputs = targets.iter().position(|t| *t == "outputs").unwrap();
        assert_eq!(&targets[outputs + 1..], ["validate", "run"]);
        for step in &SETUP[outputs..] {
            assert_eq!(step.page, Page::Outputs);
        }
    }
    #[test]
    fn results_tour_visits_each_results_page_once_and_ends_on_the_result_bar() {
        let targets: Vec<_> = RESULTS.iter().map(|s| s.target).collect();
        assert_eq!(
            targets,
            [
                "result_open",
                "chart",
                "tables",
                "photons",
                "pathways",
                "ledger",
                "export"
            ]
        );
        assert_eq!(RESULTS[0].page, RESULTS[6].page);
    }
    #[test]
    fn walkthrough_accepts_next_and_escape_input() {
        let ctx = egui::Context::default();
        let mut tour = Tour::default();
        tour.start(false);
        tour.mark(
            "open",
            Rect::from_min_size(egui::pos2(40., 40.), Vec2::new(100., 30.)),
        );
        let frame = |tour: &mut Tour, events: Vec<egui::Event>, time: f64| {
            let input = egui::RawInput {
                screen_rect: Some(Rect::from_min_size(egui::Pos2::ZERO, Vec2::new(900., 700.))),
                events,
                time: Some(time),
                ..Default::default()
            };
            let mut output = ctx.run_ui(input, |_| tour.show(&ctx));
            output.textures_delta.clear();
        };
        frame(&mut tour, vec![], 0.);
        frame(&mut tour, vec![], 0.1);
        let pos = tour.anchors["tour_next"].center();
        frame(
            &mut tour,
            vec![
                egui::Event::PointerMoved(pos),
                egui::Event::PointerButton {
                    pos,
                    button: egui::PointerButton::Primary,
                    pressed: true,
                    modifiers: Default::default(),
                },
            ],
            0.2,
        );
        frame(
            &mut tour,
            vec![egui::Event::PointerButton {
                pos,
                button: egui::PointerButton::Primary,
                pressed: false,
                modifiers: Default::default(),
            }],
            0.25,
        );
        assert_eq!(
            tour.active,
            Some((false, 1)),
            "Next must be clickable above the dimming layer"
        );
        frame(
            &mut tour,
            vec![egui::Event::Key {
                key: egui::Key::Escape,
                physical_key: None,
                pressed: true,
                repeat: false,
                modifiers: Default::default(),
            }],
            0.3,
        );
        assert!(tour.active.is_none());
    }
}
