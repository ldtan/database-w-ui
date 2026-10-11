import { useState } from "react";
import { AppDialog } from "../../components/AppDialog";
import { TabPanel, TabView } from "primereact/tabview";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import type { Column, Form, Metric, View, ViewConfig } from "../../lib/types";
import { ColumnsPanel } from "../columns/ColumnsPanel";
import { FormsEditor } from "../forms/FormsEditor";
import { SharePanel } from "../permissions/SharePanel";
import { MetricsPanel } from "../metrics/MetricsPanel";
import { ViewsPanel, type ViewControlsState } from "../views/ViewsPanel";

interface Props {
  tableUuid: string;
  tableName: string;
  workspaceUuid: string | null;
  columns: Column[];
  forms: Form[];
  views: View[];
  metrics: Metric[];
  activeViewUuid: string | null;
  onSelectView: (uuid: string | null) => void;
  // The sort/filter/columns showing right now — what a new view is created from.
  currentConfig: ViewConfig;
  viewControls: ViewControlsState;
  canConfigure: boolean;
  canShare: boolean;
  // The table's own "unique permissions" break from its workspace.
  inherit: { value: boolean; onChange: (next: boolean) => void; busy?: boolean };
  visible: boolean;
  onHide: () => void;
}

/** Everything that configures the table itself — its fields, who may see it, and the
 *  layout of its forms — behind one button, so the page's toolbar is about the data
 *  rather than about the schema.
 *
 *  Record-level sharing is deliberately *not* here: it belongs to a row, not to the
 *  table, and stays on the row (see the share button per record).
 */
export function TableSettingsDialog({
  tableUuid,
  tableName,
  workspaceUuid,
  columns,
  forms,
  views,
  metrics,
  activeViewUuid,
  onSelectView,
  currentConfig,
  viewControls,
  canConfigure,
  canShare,
  inherit,
  visible,
  onHide,
}: Props) {
  const [tab, setTab] = useState(0);

  // A tab the caller may not use is not rendered at all, so the indices below are
  // built from what is actually shown rather than assumed.
  const tabs = [
    {
      key: "columns",
      header: "Columns",
      body: (
        <ColumnsPanel
          tableUuid={tableUuid}
          workspaceUuid={workspaceUuid}
          columns={columns}
          canConfigure={canConfigure}
        />
      ),
    },
    ...(canShare
      ? [
          {
            key: "permissions",
            header: "Permissions",
            body: (
              <SharePanel
                resourceType="table"
                resourceUuid={tableUuid}
                inherit={inherit}
                active={visible}
              />
            ),
          },
        ]
      : []),
    {
      key: "views",
      header: "Views",
      body: (
        <ViewsPanel
          tableUuid={tableUuid}
          views={views}
          activeUuid={activeViewUuid}
          onSelect={onSelectView}
          currentConfig={currentConfig}
          controls={viewControls}
          canConfigure={canConfigure}
        />
      ),
    },
    {
      key: "forms",
      header: "Forms",
      body: (
        <FormsEditor
          tableUuid={tableUuid}
          columns={columns}
          forms={forms}
          active={visible}
          canConfigure={canConfigure}
        />
      ),
    },
    {
      key: "metrics",
      header: "Metrics",
      body: (
        <MetricsPanel
          tableUuid={tableUuid}
          metrics={metrics}
          canConfigure={canConfigure}
        />
      ),
    },
  ];

  return (
    <AppDialog
      header={`${tableName} settings`}
      visible={visible}
      onHide={onHide}
      style={{ width: 720 }}
      // The tab strip sits flush against the header rule rather than floating
      // in the body's padding, which is what made it read as a widget dropped
      // into the dialog instead of as the dialog's own navigation.
      contentStyle={{ padding: 0 }}
      footer={<Button label="Done" text onClick={onHide} />}
    >
      <TabView activeIndex={tab} onTabChange={(e) => setTab(e.index)}>
        {tabs.map((t) => (
          <TabPanel key={t.key} header={t.header}>
            {!canConfigure && !canShare && (
              <Message
                className="mb-3"
                severity="info"
                text="You can view this table's settings but not change them."
              />
            )}
            {t.body}
          </TabPanel>
        ))}
      </TabView>
    </AppDialog>
  );
}
