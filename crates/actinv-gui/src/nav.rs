//! Pages of the shells, their order, labels and sidebar groups. Code addresses a page by `Page`,
//! never by position, so adding a page cannot silently retarget another.
//!
//! The order is the path a user walks: Start, the numbered setup steps ending in Check & run,
//! the results (Summary first), studies that run many calculations, and tools.

/// Sidebar sections, in display order.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Group {
    /// The home page.
    Start,
    /// Pages that edit the problem, in the order a calculation is set up. They pause while raw
    /// JSON edits are pending.
    Setup,
    /// Pages that read a result.
    Results,
    /// Pages that run many calculations.
    Studies,
    /// Pages that work on the whole document.
    Tools,
}

impl Group {
    /// Sidebar caption; `None` for a group listed without one.
    pub fn caption(self) -> Option<&'static str> {
        match self {
            Group::Start => None,
            Group::Setup => Some("SET UP"),
            Group::Results => Some("RESULTS"),
            Group::Studies => Some("STUDIES"),
            Group::Tools => Some("TOOLS"),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Page {
    Start,
    Material,
    Schedule,
    Spectrum,
    Outputs,
    CheckRun,
    Summary,
    Charts,
    Tables,
    Photons,
    Pathways,
    Waste,
    Ledger,
    Export,
    Sweep,
    Assimilation,
    Advanced,
}

impl Page {
    /// Every page in sidebar order. Pages of one group are contiguous.
    pub const ALL: [Page; 17] = [
        Page::Start,
        Page::Material,
        Page::Schedule,
        Page::Spectrum,
        Page::Outputs,
        Page::CheckRun,
        Page::Summary,
        Page::Charts,
        Page::Tables,
        Page::Photons,
        Page::Pathways,
        Page::Waste,
        Page::Ledger,
        Page::Export,
        Page::Sweep,
        Page::Assimilation,
        Page::Advanced,
    ];

    /// The setup steps in the order the flow walks them.
    pub const STEPS: [Page; 5] = [
        Page::Material,
        Page::Schedule,
        Page::Spectrum,
        Page::Outputs,
        Page::CheckRun,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Page::Start => "Start",
            Page::Material => "Material",
            Page::Schedule => "Irradiation & cooling",
            Page::Spectrum => "Source & spectrum",
            Page::Outputs => "What to compute",
            Page::CheckRun => "Check & run",
            Page::Summary => "Summary",
            Page::Charts => "Time history",
            Page::Tables => "Nuclides",
            Page::Photons => "Photons & dose",
            Page::Pathways => "Pathways",
            Page::Waste => "Waste class",
            Page::Ledger => "Evidence",
            Page::Export => "Export",
            Page::Sweep => "Sweep",
            Page::Assimilation => "Assimilation",
            Page::Advanced => "Advanced JSON",
        }
    }

    pub fn group(self) -> Group {
        match self {
            Page::Start => Group::Start,
            Page::Material | Page::Schedule | Page::Spectrum | Page::Outputs | Page::CheckRun => {
                Group::Setup
            }
            Page::Summary
            | Page::Charts
            | Page::Tables
            | Page::Photons
            | Page::Pathways
            | Page::Waste
            | Page::Ledger
            | Page::Export => Group::Results,
            Page::Sweep | Page::Assimilation => Group::Studies,
            Page::Advanced => Group::Tools,
        }
    }

    /// The step number of a setup page, counted from 1.
    pub fn step(self) -> Option<usize> {
        Page::STEPS.iter().position(|p| *p == self).map(|i| i + 1)
    }

    /// The setup step after this one; Check & run is the last.
    pub fn next_step(self) -> Option<Page> {
        let i = Page::STEPS.iter().position(|p| *p == self)?;
        Page::STEPS.get(i + 1).copied()
    }

    /// Pages that read the loaded result and show an empty state without one. The sidebar dims
    /// them while no result is loaded.
    pub fn needs_result(self) -> bool {
        self.group() == Group::Results
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pages_keep_their_order_and_labels() {
        let labels: Vec<_> = Page::ALL.iter().map(|p| p.label()).collect();
        assert_eq!(
            labels,
            [
                "Start",
                "Material",
                "Irradiation & cooling",
                "Source & spectrum",
                "What to compute",
                "Check & run",
                "Summary",
                "Time history",
                "Nuclides",
                "Photons & dose",
                "Pathways",
                "Waste class",
                "Evidence",
                "Export",
                "Sweep",
                "Assimilation",
                "Advanced JSON",
            ]
        );
    }

    #[test]
    fn groups_are_contiguous_and_match_the_sidebar_sections() {
        let groups: Vec<_> = Page::ALL.iter().map(|p| p.group()).collect();
        let mut expected = vec![Group::Start];
        expected.extend([Group::Setup; 5]);
        expected.extend([Group::Results; 8]);
        expected.extend([Group::Studies; 2]);
        expected.push(Group::Tools);
        assert_eq!(groups, expected);
        assert_eq!(Group::Start.caption(), None);
        assert_eq!(Group::Setup.caption(), Some("SET UP"));
        assert_eq!(Group::Results.caption(), Some("RESULTS"));
        assert_eq!(Group::Studies.caption(), Some("STUDIES"));
        assert_eq!(Group::Tools.caption(), Some("TOOLS"));
    }

    #[test]
    fn every_page_is_listed_exactly_once() {
        for page in Page::ALL {
            assert_eq!(Page::ALL.iter().filter(|p| **p == page).count(), 1);
        }
    }

    #[test]
    fn setup_steps_are_numbered_in_sidebar_order_and_end_at_check_and_run() {
        let setup: Vec<_> = Page::ALL
            .into_iter()
            .filter(|p| p.group() == Group::Setup)
            .collect();
        assert_eq!(setup, Page::STEPS);
        for (i, page) in Page::STEPS.iter().enumerate() {
            assert_eq!(page.step(), Some(i + 1));
        }
        assert_eq!(Page::Material.next_step(), Some(Page::Schedule));
        assert_eq!(Page::Outputs.next_step(), Some(Page::CheckRun));
        assert_eq!(Page::CheckRun.next_step(), None);
        assert_eq!(Page::Summary.step(), None);
    }

    #[test]
    fn only_result_pages_need_a_result_and_summary_comes_first() {
        let needing: Vec<_> = Page::ALL.into_iter().filter(|p| p.needs_result()).collect();
        assert_eq!(
            needing,
            [
                Page::Summary,
                Page::Charts,
                Page::Tables,
                Page::Photons,
                Page::Pathways,
                Page::Waste,
                Page::Ledger,
                Page::Export,
            ]
        );
    }
}
