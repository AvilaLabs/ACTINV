#!/usr/bin/env python3
"""Render the verified TCS-1 results as HTML, Word and PDF using LibreOffice."""
import hashlib
import html
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'work/matplotlib'))
sys.path.insert(0, str(ROOT/'supplement'))
import compare
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TITLE = 'Threshold inconsistencies in TENDL-2025 neutron production data: reproducible diagnosis and reaction-rate sensitivity'


def fmt(x):
    return f'{x:.6g}'


def make_content():
    raw = (ROOT/'supplement/comparison.json').read_bytes()
    r = json.loads(raw)
    checks = json.loads((ROOT/'supplement/check.json').read_text())
    assert checks['pass'] and checks['comparison_sha256'] == hashlib.sha256(raw).hexdigest()
    rows = r['cases']
    labels = ['Fe-53m', 'Cl-35', 'Zr-88', 'Y-88']
    table1 = ''.join(f'<tr><td>{name}</td><td>{int(c["energy_eV"])/1e6:.6f}</td><td>0</td><td>{fmt(float(c["records"]["10"]["value_b"]))}</td><td>{(c["next_energy_eV"]-int(c["energy_eV"]))/1000:.3f}</td></tr>' for name,c in zip(labels,rows))
    table2 = ''
    for name,c in zip(labels,rows):
        for spectrum, short in [('uniform_0_20MeV','Uniform'),('gaussian_14MeV_sd0.5MeV','Gaussian')]:
            v = c['folds'][spectrum]
            pct = 'Below absolute check tolerance' if v['delta_b_direct'] < 1e-13 else fmt(100*v['delta_over_corrected'])
            table2 += f'<tr><td>{name}</td><td>{short}</td><td>{fmt(v["original_b"])}</td><td>{fmt(v["corrected_b"])}</td><td>{pct}</td></tr>'
    table3 = ''.join(f'<tr><td>{name}</td><td>{fmt(c["original_at_14.1MeV_b"])}</td><td>{fmt(c["corrected_at_14.1MeV_b"])}</td></tr>' for name,c in zip(labels,rows))
    c = rows[1]
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.75), layout='constrained')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9})
    a, b = int(c['energy_eV'])/1e6, c['next_energy_eV']/1e6
    y, yy = float(c['records']['10']['value_b']), c['next_ordinate_b']
    axes[0].plot([a,b],[y,yy],color='#9e352c',lw=2,label='Original')
    axes[0].plot([a,b],[0,yy],color='#176378',lw=2,ls='--',label='Zeroed threshold')
    axes[0].fill_between([a,b],[y,yy],[0,yy],color='#9e352c',alpha=.12)
    axes[0].set(xlabel='Neutron energy (MeV)',ylabel='Ground-state production (b)',title='(a) Cl-35 first interval',xlim=(a,b),ylim=(-.5,26))
    axes[0].legend(fontsize=8,frameon=False)
    for i,key in enumerate(('uniform_0_20MeV','gaussian_14MeV_sd0.5MeV')):
        v=c['folds'][key]
        axes[1].bar(i-.18,v['original_b'],width=.34,color='#9e352c',label='Original' if i==0 else None)
        axes[1].bar(i+.18,v['corrected_b'],width=.34,color='#176378',label='Zeroed threshold' if i==0 else None)
    axes[1].set(yscale='log',ylabel='Spectrum-averaged production (b)',title='(b) Cl-35 spectrum folds',xticks=[0,1],xticklabels=['Uniform','Gaussian'],ylim=(5e-4,3))
    axes[1].legend(fontsize=8,frameon=False,loc='upper left')
    for ax in axes:
        ax.tick_params(labelsize=8)
        ax.title.set_fontsize(10)
        ax.xaxis.label.set_fontsize(9)
        ax.yaxis.label.set_fontsize(9)
        ax.spines[['top','right']].set_visible(False)
    for ext in ('png','svg','pdf'):
        fig.savefig(ROOT/f'figures/threshold_sensitivity.{ext}',dpi=300)
    plt.close(fig)
    main = f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{TITLE}</title>
<style>
@page {{ size:A4; margin:25.4mm; }}
body {{font-family:"Times New Roman",serif;font-size:12pt;line-height:1.35;color:#111;max-width:170mm;margin:auto}}
h1 {{font-size:14pt; margin-top:18pt; page-break-after:avoid}} h2 {{font-size:12pt; margin-top:12pt;page-break-after:avoid}}
p {{margin:0 0 9pt}} .title {{font-size:19pt;font-weight:bold;line-height:1.15;margin:10pt 0 15pt}}
.meta {{font-size:10pt}} .kind {{font-size:10pt;text-transform:uppercase;letter-spacing:1pt}}
table {{border-collapse:collapse;width:100%;font-size:10pt;margin:8pt 0 8pt;page-break-inside:avoid}}
th,td {{padding:5pt 4pt;border-bottom:0.5pt solid #bbb;text-align:left}} th {{border-top:1pt solid #222;border-bottom:1pt solid #222}}
.caption {{font-size:10pt;line-height:1.2;page-break-after:avoid}} .figurecaption {{font-size:10pt;line-height:1.2}}
.equation {{text-align:center;margin:12pt 0;font-size:12pt}} .references {{font-size:10pt;line-height:1.2}}
img {{width:165mm;height:auto}} a {{color:#17445e;text-decoration:none}}
</style></head><body>
<p class="kind">Technical note · Author-review draft · 14 September 2026</p>
<p class="title">{TITLE}</p>
<p><b>Connor Avila</b></p>
<p class="meta">Avila Labs, Oviedo, FL, USA<br>Correspondence: research@avilalabs.org<br>ORCID: 0009-0000-9957-9857</p>
<h1>Abstract</h1>
<p>Four archived TENDL-2025 neutron evaluations contain nonzero ground-state (n,2n) production cross sections at explicitly tabulated energies where their corresponding reaction totals are zero. The affected targets are Fe-53m, Cl-35, Zr-88, and Y-88. Source hashes, fixed-width records, and a standalone decimal parser establish these contradictions independently of an inventory solver. The maintainer reported an uncleared TALYS work array as the cause and a correction intended for the next TENDL release. A controlled sensitivity calculation replaces only the contaminated first production ordinate with zero and integrates the original and modified tables under two prescribed spectra. For Cl-35, a normalized Gaussian centered at 14 MeV with a 0.5 MeV standard deviation gives 1.28083 b before the intervention and 0.00114522 b afterward, a ratio of approximately 1118. All four point values at 14.1 MeV are unchanged. Independent parsing and numerical quadrature agree with the analytic folds to within 7.4 × 10<sup>−15</sup> b. These results demonstrate spectrum-dependent propagation of a threshold-record defect and support explicit cross-file consistency checks. The modified tables are experimental variants, not qualified replacement evaluations.</p>
<p class="meta"><b>Keywords:</b> TENDL-2025; ENDF-6; neutron activation; threshold consistency; reaction-rate sensitivity; reproducibility</p>
<h1>1 Introduction</h1>
<p>TENDL is a TALYS-based evaluated nuclear-data library whose production workflow emphasizes completeness, automation, and reproducibility [1]. These properties make reproducible checks of the distributed records useful alongside experimental validation. In particular, a reaction cross section and the cross section for producing one of its residual states provide an internal consistency relation that can be checked without selecting an external benchmark.</p>
<p>The ENDF-6 manual specifies that a negative-Q File 10 subsection begins at threshold with zero cross section. It also requires File 10 production cross sections not to exceed the corresponding File 3 reaction cross section [2, Sect. 10.3]. Here, “total” means the total for the same reaction number, not the total neutron interaction cross section.</p>
<p>An investigation during ACTINV development identified four matching-energy violations of both conditions in archived TENDL-2025 neutron files [3]. This note documents those cases and measures the effect of a narrowly defined correction on spectrum-averaged production. A separate submitted paper describes ACTINV v1.0.0 and its software-validation baseline [4]; the present note reports a subsequent source-data investigation and does not reproduce that paper’s experimental benchmark results.</p>
<h1>2 Materials and methods</h1>
<h2>2.1 Source identity and explicit-record checks</h2>
<p>The inputs are four files from the locally archived TENDL-2025 neutron s30 distribution. The acquisition record identifies the archive as TENDL-n.tgz, 3,517,450,425 bytes, with SHA-256 e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa. Full per-file digests and exact records are provided in the supplement. The archive itself was not downloaded or rehashed for this study; each selected local file was rehashed and matched to the previously recorded identity. The conclusions therefore concern these exact bytes and do not establish the state of a subsequently replaced download.</p>
<p>A standalone Python reproducer reads the original 80-column records using decimal arithmetic. For each case it checks the file digest, MF=3/MT=16 and MF=10/MT=16 sections, ground-state product identifier, negative reaction Q value, and matching first tabulated energies. Here MF identifies the ENDF file type, MT=16 identifies (n,2n), and LFS=0 identifies the ground-state product. No interpolation is needed to establish the four contradictions in Table 1.</p>
<p class="caption"><b>Table 1.</b> Explicit threshold contradictions. The final column is the width to the next ground-state production point; 1 b = 10<sup>−24</sup> cm<sup>2</sup>.</p>
<table><thead><tr><th>Target</th><th>Energy (MeV)</th><th>MF=3 total (b)</th><th>MF=10 ground state (b)</th><th>First interval (keV)</th></tr></thead><tbody>{table1}</tbody></table>
<h2>2.2 One-ordinate intervention</h2>
<p>The study protocol was fixed before the new calculation. In each file, the intervention changes only columns 12–22 of the first ground-state MF=10/MT=16 data record to the ENDF representation of zero. All other bytes, including the energy grid, later ordinates, other states, and MF=3 totals, remain unchanged. The program verifies that the byte differences are confined to this field and records a full-file digest for the in-memory variant. No installed evaluation or processed library is modified.</p>
<p>This intervention isolates the contribution of the reported threshold point. It is consistent with the zero-at-threshold requirement, but it does not establish that every subsequent value or other section in the modified evaluation is correct. In the remainder of this note, “corrected” denotes this single-ordinate experimental variant.</p>
<h2>2.3 Spectrum folds and production rates</h2>
<p>All selected subsections declare linear-linear interpolation. If the first two energies are E<sub>0</sub> and E<sub>1</sub> and the contaminated first value is A, the removed contribution is triangular:</p>
<p class="equation">Δσ(E) = A(E<sub>1</sub> − E)/(E<sub>1</sub> − E<sub>0</sub>), &nbsp; E<sub>0</sub> ≤ E ≤ E<sub>1</sub>. &nbsp; (1)</p>
<p>It is zero elsewhere. The unweighted removed area is A(E<sub>1</sub> − E<sub>0</sub>)/2. For a normalized spectral density p(E), the spectrum-averaged ground-state production cross section and rate per target nucleus are</p>
<p class="equation">σ̄ = ∫<sub>0</sub><sup>20 MeV</sup> σ(E)p(E) dE, &nbsp; r = Φ × 10<sup>−24</sup> σ̄. &nbsp; (2)</p>
<p>Cross sections in Eq. (2) are in barns and the integrated flux Φ is in cm<sup>−2</sup> s<sup>−1</sup>. Two spectra are prescribed: a uniform energy density on 0–20 MeV, and a Gaussian with mean 14 MeV and standard deviation 0.5 MeV, normalized on the same interval. These are illustrative sensitivity spectra, not measured reactor or neutron-source spectra. Below the negative-Q threshold the cross section is set to zero, and source coverage through 20 MeV is required. A separate point evaluation at 14.1 MeV provides a control outside every altered interval. Rate examples use Φ = 10<sup>14</sup> cm<sup>−2</sup> s<sup>−1</sup>.</p>
<p>The primary calculation integrates linear segments analytically, using Gaussian probability and first moments where needed. Equal-energy records have zero integration width; exact point queries use the last value at that energy. The removed contribution is integrated directly to avoid subtracting nearly equal folds. A separately implemented fixed-field parser and segmentwise adaptive quadrature check the original, corrected, and removed contributions. Exact-decimal triangle areas check the uniform-spectrum differences. Constant and linear fixtures and a deliberately corrupted source-hash check also pass. The frozen comparison tolerance is 10<sup>−9</sup> relative or 10<sup>−13</sup> b absolute; very small Gaussian differences are not claimed to be resolved by subtraction.</p>
<h1>3 Results</h1>
<h2>3.1 Reproduction and spectrum dependence</h2>
<p>All four source contradictions reproduced with matching full-file hashes. Table 2 shows the complete prescribed spectrum comparison. The largest uniform-spectrum sensitivity occurs for Fe-53m: 21.5902 b in the original table versus 0.00384726 b in the variant. Its contaminated interval is only 8.026 keV wide, but the first ordinate is 107,582 b. Fe-53m is a metastable target; this result must not be interpreted as an effect in ordinary bulk iron.</p>
<p class="caption"><b>Table 2.</b> Spectrum-averaged ground-state (n,2n) production. Relative excess is 100(σ̄<sub>original</sub> − σ̄<sub>corrected</sub>)/σ̄<sub>corrected</sub>; it is a sensitivity to the intervention, not an experimentally established error.</p>
<table><thead><tr><th>Target</th><th>Spectrum</th><th>Original (b)</th><th>Corrected (b)</th><th>Relative excess (%)</th></tr></thead><tbody>{table2}</tbody></table>
<p>Cl-35 has a wider contaminated interval, 13.0095–13.5 MeV, which overlaps the low-energy tail of the prescribed Gaussian. Its averaged ground-state production changes from 1.28083 to 0.00114522 b, an original-to-corrected ratio of approximately 1118. For the uniform spectrum the corresponding ratio is approximately 89.1. Figure 1 shows the affected interval and the two folds.</p>
<p><img src="figures/threshold_sensitivity.png" alt="Cl-35 original and corrected first interval, and their uniform and Gaussian spectrum folds"></p>
<p class="figurecaption"><b>Fig. 1.</b> Cl-35 sensitivity to zeroing the first ground-state production ordinate. (a) The shaded area is the removed triangular contribution; the corrected curve is near zero on this linear scale. (b) Spectrum-averaged production under the prescribed uniform and Gaussian spectra; the ordinate is logarithmic. Both panels concern ground-state production, not the MF=3 reaction total.</p>
<p>Zr-88 changes by 5.013% relative to the corrected value for the uniform spectrum and 0.5142% for the Gaussian. Y-88 changes by 22.00% for the uniform spectrum. The Fe-53m and Y-88 Gaussian differences are below the 10<sup>−13</sup> b absolute check tolerance and below the precision of subtraction of the reported folds. Their direct triangular integrals are retained in the machine-readable supplement without assigning validated relative precision to these tiny differences.</p>
<h2>3.2 Rate consequences and point control</h2>
<p>At Φ = 10<sup>14</sup> cm<sup>−2</sup> s<sup>−1</sup>, the Cl-35 Gaussian example gives original and corrected ground-state production rates of 1.28083 × 10<sup>−10</sup> and 1.14522 × 10<sup>−13</sup> s<sup>−1</sup> per target nucleus. The absolute change is 1.27968 × 10<sup>−10</sup> s<sup>−1</sup>. These rates are direct source-term sensitivities. Converting them into activities or inventories requires target populations, irradiation history, decay, depletion, and competing production and removal pathways, which are outside this calculation.</p>
<p>All four cross sections evaluated at exactly 14.1 MeV are unchanged (Table 3), since that energy lies above each modified interval. This control demonstrates why agreement at one incident energy cannot bound the effect under a distributed spectrum.</p>
<p class="caption"><b>Table 3.</b> Unchanged ground-state production point values at 14.1 MeV.</p>
<table><thead><tr><th>Target</th><th>Original (b)</th><th>Corrected (b)</th></tr></thead><tbody>{table3}</tbody></table>
<p>The maximum absolute disagreement between the analytic folds and the independent quadrature checks was 7.325 × 10<sup>−15</sup> b. The independent checks also reproduced the original and variant hashes, the one-field intervention, and the unchanged point values. The original MF=3 tables were folded separately as conservation comparators; they were not used as replacement ground-state production data.</p>
<h1>4 Discussion</h1>
<h2>4.1 Reported cause and correction status</h2>
<p>In private correspondence dated 14 September 2026, A. Koning confirmed the defect class and reported an uncleared work array in the TALYS subroutine channelsout.f90. According to that response, a thermal (n,p) cross section was written into a ground-state (n,2n) production record. The maintainer reported a TALYS fix intended to appear in the next TENDL release and suggested zeroing contaminated leading energy points as an interim manual action. This cause and release status are attributed to the correspondence; the present study does not independently inspect the TALYS fix or test a newly issued official evaluation.</p>
<p>The single-ordinate intervention offers a reproducible measurement of one consequence of the reported mechanism. It is deliberately narrower than qualifying an entire repaired file. In particular, the predicted magnitudes depend on the spectrum and do not imply comparable errors for all users of these evaluations.</p>
<h2>4.2 Scope of the evidence</h2>
<p>The matching explicit ordinates make the four primary cases independent of interpolation choices or numerical precision. The rate calculation then shows how a bad ordinate extends over a finite interval under the declared interpolation law. Source-record magnitude alone is insufficient to rank application impact: the large Fe-53m point has negligible weight under the chosen Gaussian, whereas the smaller Cl-35 point strongly affects that fold.</p>
<p>The broader ACTINV audit contains additional construction failures, interpolation-related findings, and processing corrections. Those historical classifications are not a census of independently confirmed defects attributable to this work-array mechanism. Likewise, a rejected target file does not establish that every reaction in the file is inaccurate. This note makes no corpus-wide prevalence estimate, solver-superiority claim, or experimental accuracy claim for the corrected variants.</p>
<p>A useful processing check combines explicit threshold validation with comparisons between state production and the corresponding reaction total. Checks at shared explicit energies provide direct evidence; checks between points additionally depend on interpolation and domain conventions. A corrected upstream release should be checked against the same source-level predicates and then qualified for its intended applications under its own identity.</p>
<h1>5 Conclusions</h1>
<p>Four archived TENDL-2025 neutron files contain independently reproducible threshold contradictions between ground-state (n,2n) production and the corresponding reaction totals. A one-ordinate intervention produces strongly spectrum-dependent changes in calculated production rates, including an approximately 1118-fold original-to-corrected ratio for Cl-35 under the prescribed Gaussian, while leaving every tested 14.1 MeV point value unchanged. The results support source-level threshold checks and spectrum-aware sensitivity analysis. They do not qualify the experimental variants as replacement nuclear-data evaluations.</p>
<h1>Declarations</h1>
<p class="meta"><b>Data and code availability.</b> The accompanying supplement contains the frozen protocol, standalone reproducer, primary comparison, independent checker, full numerical results, source and variant digests, and exact changed records. Original evaluations are obtained from the TENDL distribution [3]; bulk nuclear-data files are not included. No persistent identifier has yet been assigned to this study’s supplement.</p>
<p class="meta"><b>Related submission.</b> The ACTINV v1.0.0 software manuscript [4] is under consideration at the Journal of Radioanalytical and Nuclear Chemistry. The present work reports a distinct subsequent investigation; its primary source cases and controlled sensitivity calculations are not presented in that manuscript.</p>
<p class="meta"><b>AI assistance.</b> OpenAI Codex assisted with analysis code, numerical checks, document preparation, and drafting. The author is responsible for final verification of the calculations, sources, interpretation, and manuscript.</p>
<h1>References</h1>
<div class="references">
<p>[1] Koning AJ, Rochman D, Sublet J-Ch, Dzysiuk N, Fleming M, van der Marck S (2019) TENDL: Complete nuclear data library for innovative nuclear science and technology. Nuclear Data Sheets 155:1–55. <a href="https://doi.org/10.1016/j.nds.2019.01.002">https://doi.org/10.1016/j.nds.2019.01.002</a></p>
<p>[2] Cross Sections Evaluation Working Group (2023) ENDF-6 Formats Manual: Data Formats and Procedures for the Evaluated Nuclear Data Files ENDF/B-VI, ENDF/B-VII and ENDF/B-VIII. Brown DA (ed). BNL-224854-2023-INRE, ENDF-102, 28 September 2023, Sect. 10.3, pp. 193–194. <a href="https://www.nndc.bnl.gov/endfdocs/ENDF-102-2023.pdf">https://www.nndc.bnl.gov/endfdocs/ENDF-102-2023.pdf</a></p>
<p>[3] TENDL collaboration (2025) TENDL-2025 nuclear data library, neutron s30 ENDF distribution. <a href="https://tendl.imperial.ac.uk/tendl_2025/tendl2025.html">https://tendl.imperial.ac.uk/tendl_2025/tendl2025.html</a>. Landing page accessed 14 September 2026; exact archived file identities are supplied in the supplement.</p>
<p>[4] Avila C (2026) ACTINV: an open and reproducible activation and nuclide-inventory solver with executable validation. Manuscript submitted to the Journal of Radioanalytical and Nuclear Chemistry; unpublished.</p>
</div></body></html>'''
    (ROOT/'manuscript.html').write_text(main)
    # Full SHA-256 strings stay in the supplement rather than narrow main-text tables.
    details = []
    for c in rows:
        details.append(f"## {c['file']}\n\nOriginal SHA-256: `{c['sha256']}`\n\nExperimental variant SHA-256: `{c['variant_sha256']}`\n\nChanged line {c['changed_field']['line_1based']}, columns 12–22 only.\n\n```text\n{c['changed_field']['before']}\n{c['changed_field']['after']}\n```\n")
    (ROOT/'supplement/SOURCE_IDENTITIES.md').write_text('# Source and experimental variant identities\n\n'+ '\n'.join(details))
    print('Manuscript HTML and figure generated from verified results.')


def prop(name, value):
    from com.sun.star.beans import PropertyValue
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def documents():
    import uno
    pipe = f'tcs_paper_{os.getpid()}_{time.time_ns()}'
    with tempfile.TemporaryDirectory(prefix='lo-',dir=ROOT/'work') as profile:
        command = ['libreoffice','--headless','--nologo','--nodefault','--norestore',
                   f'-env:UserInstallation={Path(profile).as_uri()}',
                   f'--accept=pipe,name={pipe};urp;StarOffice.ServiceManager']
        office = subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        doc = None
        try:
            local = uno.getComponentContext()
            resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver',local)
            deadline = time.monotonic()+30
            while True:
                try:
                    ctx = resolver.resolve(f'uno:pipe,name={pipe};urp;StarOffice.ComponentContext')
                    break
                except Exception:
                    if time.monotonic()>deadline or office.poll() is not None:
                        raise RuntimeError('LibreOffice startup failed')
                    time.sleep(.25)
            sm = ctx.ServiceManager
            desktop = sm.createInstanceWithContext('com.sun.star.frame.Desktop',ctx)
            doc = desktop.loadComponentFromURL((ROOT/'manuscript.html').as_uri(),'_blank',0,(prop('Hidden',True),))
            assert doc is not None
            families = doc.getStyleFamilies()
            for name in families.getByName('ParagraphStyles').getElementNames():
                style = families.getByName('ParagraphStyles').getByName(name)
                style.CharFontName='Times New Roman'
            tables=doc.getTextTables()
            assert len(tables.getElementNames()) == 3
            widths=[[1100,1900,1700,2900,2400], [1100,1600,1900,1900,3500], [2000,4000,4000]]
            for idx,name in enumerate(tables.getElementNames()):
                table=tables.getByName(name)
                table.Split=False
                table.RepeatHeadline=True
                table.HeaderRowCount=1
                separators=table.TableColumnSeparators
                running=0
                for j,sep in enumerate(separators):
                    running += widths[idx][j]
                    sep.Position=round(running*table.TableColumnRelativeSum/10000)
                table.TableColumnSeparators=separators
                border=uno.createUnoStruct('com.sun.star.table.TableBorder2')
                line=uno.createUnoStruct('com.sun.star.table.BorderLine2')
                line.Color=0x777777
                line.LineWidth=15
                for edge in ('TopLine','BottomLine','HorizontalLine'):
                    setattr(border,edge,line)
                    setattr(border,'Is'+edge+'Valid',True)
                table.TableBorder2=border
                distances=uno.createUnoStruct('com.sun.star.table.TableBorderDistances')
                for edge in ('Top','Bottom','Left','Right'):
                    setattr(distances,edge+'Distance',100)
                    setattr(distances,'Is'+edge+'DistanceValid',True)
                table.TableBorderDistances=distances
                for cellname in table.getCellNames():
                    cell=table.getCellByName(cellname)
                    cur=cell.createTextCursor()
                    cur.gotoEnd(True)
                    cur.CharHeight=10
                    cur.ParaTopMargin=50
                    cur.ParaBottomMargin=50
                    if cellname.rstrip('0123456789')+'1' == cellname:
                        cell.BackColor=0xF0F2F4
                for rownum in range(table.Rows.Count):
                    table.Rows.getByIndex(rownum).IsSplitAllowed=False
            page_styles = families.getByName('PageStyles')
            for name in page_styles.getElementNames():
                st=page_styles.getByName(name)
                st.Width,st.Height=21000,29700
                st.TopMargin=st.BottomMargin=st.LeftMargin=st.RightMargin=2540
                st.FooterIsOn=True
                footer=st.FooterText
                footer.setString('')
                cur=footer.createTextCursor()
                cur.ParaAdjust=3
                cur.CharHeight=10
                field=doc.createInstance('com.sun.star.text.TextField.PageNumber')
                field.NumberingType=4
                footer.insertTextContent(cur,field,False)
            graphics=doc.getGraphicObjects()
            assert len(graphics.getElementNames())==1
            g=graphics.getByName(graphics.getElementNames()[0])
            provider=sm.createInstanceWithContext('com.sun.star.graphic.GraphicProvider',ctx)
            g.Graphic=provider.queryGraphic((prop('URL',(ROOT/'figures/threshold_sensitivity.png').as_uri()),))
            old=g.getSize()
            from com.sun.star.awt import Size
            g.setSize(Size(15900,round(15900*old.Height/old.Width)))
            metadata=doc.getDocumentProperties()
            metadata.Title=TITLE
            metadata.Author='Connor Avila'
            metadata.Subject='Technical note; author-review draft'
            doc.storeAsURL((ROOT/'TENDL_threshold_note.docx').as_uri(),(prop('FilterName','Office Open XML Text'),prop('Overwrite',True)))
            doc.storeToURL((ROOT/'TENDL_threshold_note.pdf').as_uri(),(prop('FilterName','writer_pdf_Export'),prop('Overwrite',True)))
        finally:
            if doc is not None:
                doc.close(True)
            office.terminate()
            try:
                office.wait(timeout=10)
            except subprocess.TimeoutExpired:
                office.kill()
                office.wait(timeout=5)
    for filename in ('TENDL_threshold_note.docx','TENDL_threshold_note.pdf'):
        assert (ROOT/filename).stat().st_size>0
    print('Word and PDF generated.')


if __name__=='__main__':
    compare.limits()
    make_content()
    documents()
