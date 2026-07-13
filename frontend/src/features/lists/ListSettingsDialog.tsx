import { useState } from "react";
import { AppDialog } from "../../components/AppDialog";
import { TabPanel, TabView } from "primereact/tabview";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import type { Column, Form, View, ViewConfig } from "../../lib/types";
import { ColumnsPanel } from "../columns/ColumnsPanel";
import { FormsEditor } from "../forms/FormsEditor";
import { SharePanel } from "../permissions/SharePanel";
import { ViewsPanel, type ViewControlsState } from "../views/ViewsPanel";

interface Props {
  listUuid: string;
  listName: string;
  workspaceUuid: string | null;
  columns: Column[];
  forms: Form[];
  views: View[];
  activeViewUuid: string | null;
  onSelectView: (uuid: string | null) => void;
  // The sort/filter/columns showing right now — what a new view is created from.
  currentConfig: ViewConfig;
  viewControls: ViewControlsState;
  canConfigure: boolean;
  canShare: boolean;
  // The list's own "unique permissions" break from its workspace.
  inherit: { value: boolean; onChange: (next: boolean) => void; busy?: boolean };
  visible: boolean;
  onHide: () => void;
}

/** Everything that configures the list itself — its fields, who may see it, and the
 *  layout of its forms — behind one button, so the page's toolbar is about the data
 *  rather than about the schema.
 *
 *  Record-level sharing is deliberately *not* here: it belongs to a row, not to the
 *  list, and stays on the row (see the share button per record).
 */
export function ListSettingsDialog({
  listUuid,
  listName,
  workspaceUuid,
  columns,
  forms,
  views,
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
          listUuid={listUuid}
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
                resourceType="list"
                resourceUuid={listUuid}
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
          listUuid={listUuid}
          views={views}
          activeUuid={activeViewUuid}
          onSelect={onSelectView}
          currentConfig={currentConfig}
          controls={viewControls}
        />
      ),
    },
    {
      key: "forms",
      header: "Forms",
      body: (
        <FormsEditor
          listUuid={listUuid}
          columns={columns}
          forms={forms}
          active={visible}
        />
      ),
    },
  ];

  return (
    <AppDialog
      header={`${listName} settings`}
      visible={visible}
      onHide={onHide}
      style={{ width: 680 }}
    >
      <TabView activeIndex={tab} onTabChange={(e) => setTab(e.index)}>
        {tabs.map((t) => (
          <TabPanel key={t.key} header={t.header}>
            <div className="pt-2">{t.body}</div>
          </TabPanel>
        ))}
      </TabView>

      {!canConfigure && !canShare && (
        <Message
          severity="info"
          text="You can view this list's settings but not change them."
        />
      )}

      <div className="flex justify-end pt-2">
        <Button label="Done" text onClick={onHide} />
      </div>
    </AppDialog>
  );
}
