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
            Group::Tools => None,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Page {
    Overview,
    Material,
    Schedule,
    Spectrum,
    Results,
    Details,
    Ledger,
    Advanced,
}

impl Page {
    /// Every page in sidebar order. Pages of one group are contiguous.
    pub const ALL: [Page; 8] = [
        Page::Overview,
        Page::Material,
        Page::Schedule,
        Page::Spectrum,
        Page::Results,
        Page::Details,
        Page::Ledger,
        Page::Advanced,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Page::Overview => "Overview & data",
            Page::Material => "Material",
            Page::Schedule => "Irradiation & cooling",
            Page::Spectrum => "Spectrum",
            Page::Results => "Results",
            Page::Details => "Spectra & pathways",
            Page::Ledger => "Ledger & certificate",
            Page::Advanced => "Advanced JSON",
        }
    }

    pub fn group(self) -> Group {
        match self {
            Page::Overview | Page::Material | Page::Schedule | Page::Spectrum => Group::Setup,
            Page::Results | Page::Details | Page::Ledger => Group::Results,
            Page::Advanced => Group::Tools,
        }
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
                "Results",
                "Spectra & pathways",
                "Ledger & certificate",
                "Advanced JSON",
            ]
        );
    }

    #[test]
    fn groups_are_contiguous_and_match_the_sidebar_sections() {
        let groups: Vec<_> = Page::ALL.iter().map(|p| p.group()).collect();
        assert_eq!(
            groups,
            [
                Group::Setup,
                Group::Setup,
                Group::Setup,
                Group::Setup,
                Group::Results,
                Group::Results,
                Group::Results,
                Group::Tools,
            ]
        );
        assert_eq!(Group::Setup.caption(), Some("CALCULATION"));
        assert_eq!(Group::Results.caption(), Some("EXPLORE"));
        assert_eq!(Group::Tools.caption(), None);
    }

    #[test]
    fn every_page_is_listed_exactly_once() {
        for page in Page::ALL {
            assert_eq!(Page::ALL.iter().filter(|p| **p == page).count(), 1);
        }
    }
}
