import { ExpandToggle, ExpandableRow, SortHeader } from "insider-intel-dossier-ui";

/**
 * The EVIDENCE technique table's controls. Two affordances share a row on
 * purpose: the technique NAME navigates away to a dossier, the chevron expands
 * detail in place, so a row carrying two destinations offers two targets.
 */
export const SortableExpandableTable = () => (
  <div style={{ maxWidth: "620px" }}>
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={{ width: "44px" }} />
          <th style={{ textAlign: "left" }}>HOW THEY DID IT</th>
          <th aria-sort="descending" style={{ textAlign: "right" }}>
            <SortHeader label="CASES" active direction="desc" hint="Every case exhibiting this technique" />
          </th>
          <th aria-sort="none" style={{ textAlign: "right" }}>
            <SortHeader label="PROVEN" hint="A judge ruled it, or the insider admitted it" />
          </th>
          <th aria-sort="none" style={{ textAlign: "right" }}>
            <SortHeader label="ALLEGED" hint="One side's account so far" />
          </th>
        </tr>
      </thead>
      <tbody>
        <ExpandableRow
          span={5}
          expanded
          detail={<p style={{ margin: 0 }}>WHERE THIS TACTIC'S EVIDENCE LIVES — email logs ×89, file access logs ×63</p>}
        >
          <td>
            <ExpandToggle expanded label="Hide where this evidence lives" />
          </td>
          <td>Insider trading</td>
          <td style={{ textAlign: "right" }}>266</td>
          <td style={{ textAlign: "right" }}>32</td>
          <td style={{ textAlign: "right" }}>234</td>
        </ExpandableRow>
        <ExpandableRow span={5}>
          <td>
            <ExpandToggle expanded={false} label="Show where this tactic's evidence lives" />
          </td>
          <td>Data exfiltration</td>
          <td style={{ textAlign: "right" }}>187</td>
          <td style={{ textAlign: "right" }}>26</td>
          <td style={{ textAlign: "right" }}>161</td>
        </ExpandableRow>
      </tbody>
    </table>
  </div>
);
