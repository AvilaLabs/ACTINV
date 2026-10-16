//! Pages of the desktop shell, their order, labels and sidebar groups. Code addresses a page by
//! `Page`, never by position, so adding a page cannot silently retarget another.

/// Sidebar sections, in display order.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Group {
    /// Pages that edit the problem. They pause while raw JSON edits are pending.
    Setup,
    /// Pages that read a result.
    Results,
    /// Pages that work on the whole document.
    Tools,
}

impl Group {
    /// Sidebar caption; `None` for a group listed without one.
    pub fn caption(self) -> Option<&'static str> {
        match self {
            Group::Setup => Some("CALCULATION"),
            Group::Results => Some("EXPLORE"),
            Group::Tools => Some("TOOLS"),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Page {
    Overview,
    Material,
    Schedule,
    Spectrum,
    Outputs,
    Charts,
    Tables,
    Photons,
    Pathways,
    Ledger,
    Waste,
    Sweep,
    Assimilation,
    Advanced,
}

impl Page {
    /// Every page in sidebar order. Pages of one group are contiguous.
    pub const ALL: [Page; 14] = [
        Page::Overview,
        Page::Material,
        Page::Schedule,
        Page::Spectrum,
        Page::Outputs,
        Page::Charts,
        Page::Tables,
        Page::Photons,
        Page::Pathways,
        Page::Ledger,
        Page::Waste,
        Page::Sweep,
        Page::Assimilation,
        Page::Advanced,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Page::Overview => "Overview & data",
            Page::Material => "Material",
            Page::Schedule => "Irradiation & cooling",
            Page::Spectrum => "Spectrum",
            Page::Outputs => "Outputs",
            Page::Charts => "Charts",
            Page::Tables => "Tables",
            Page::Photons => "Photons & dose",
            Page::Pathways => "Pathways",
            Page::Ledger => "Ledger & certificate",
            Page::Waste => "Waste class",
            Page::Sweep => "Sweep",
            Page::Assimilation => "Assimilation",
            Page::Advanced => "Advanced JSON",
        }
    }

    pub fn group(self) -> Group {
        match self {
            Page::Overview | Page::Material | Page::Schedule | Page::Spectrum | Page::Outputs => {
                Group::Setup
            }
            Page::Charts
            | Page::Tables
            | Page::Photons
            | Page::Pathways
            | Page::Ledger
            | Page::Waste => Group::Results,
            Page::Sweep | Page::Assimilation | Page::Advanced => Group::Tools,
        }
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
                "Overview & data",
                "Material",
                "Irradiation & cooling",
                "Spectrum",
                "Outputs",
                "Charts",
                "Tables",
                "Photons & dose",
                "Pathways",
                "Ledger & certificate",
                "Waste class",
                "Sweep",
                "Assimilation",
                "Advanced JSON",
            ]
        );
    }

    #[test]
    fn groups_are_contiguous_and_match_the_sidebar_sections() {
        let groups: Vec<_> = Page::ALL.iter().map(|p| p.group()).collect();
        let mut expected = vec![Group::Setup; 5];
        expected.extend([Group::Results; 6]);
        expected.extend([Group::Tools; 3]);
        assert_eq!(groups, expected);
        assert_eq!(Page::ALL.len(), 14);
        assert_eq!(Group::Setup.caption(), Some("CALCULATION"));
        assert_eq!(Group::Results.caption(), Some("EXPLORE"));
        assert_eq!(Group::Tools.caption(), Some("TOOLS"));
    }

    #[test]
    fn every_page_is_listed_exactly_once() {
        for page in Page::ALL {
            assert_eq!(Page::ALL.iter().filter(|p| **p == page).count(), 1);
        }
    }

    #[test]
    fn only_result_pages_need_a_result() {
        let needing: Vec<_> = Page::ALL.into_iter().filter(|p| p.needs_result()).collect();
        assert_eq!(
            needing,
            [
                Page::Charts,
                Page::Tables,
                Page::Photons,
                Page::Pathways,
                Page::Ledger,
                Page::Waste
            ]
        );
    }
}
