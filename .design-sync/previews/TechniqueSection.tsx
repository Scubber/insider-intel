import { TechniqueSection } from "insider-intel-dossier-ui";

export const SingleCase = () => (
  <TechniqueSection
    id="IF016"
    description="A subject dishonestly makes false representations, fails to disclose information or abuses their access or position to make a financial gain."
    cases={[
      {
        title: "DictateMD, Inc. v. Ahmadi",
        bullets: [
          "Downloaded the customer database in the two weeks before resignation",
          "Synced product schematics to a personal Dropbox",
          "Detected via forensic review of the returned laptop",
        ],
      },
    ]}
  />
);

export const MultiCase = () => (
  <div style={{ display: "grid", gap: "0.8rem" }}>
    <TechniqueSection
      id="IF016.004"
      description="A subject with access to sensitive or confidential information decides to use that information to trade the company's securities."
      cases={[
        {
          title: "Securities and Exchange Commission v. Chammout",
          bullets: [
            "Traded ahead of an unannounced acquisition using deal knowledge",
            "Tipped two family members before the public filing",
          ],
        },
        {
          title: "Jenell v. Donahoe",
          bullets: ["Matched in text: insider trading"],
        },
      ]}
    />
    <TechniqueSection
      id="ME007"
      description="A subject has privileged access to devices, systems or services that hold sensitive information."
      cases={[
        {
          title: "DictateMD, Inc. v. Ahmadi",
          bullets: ["Retained admin access to the production database through the notice period"],
        },
      ]}
    />
  </div>
);
