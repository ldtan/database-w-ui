import { Dialog } from "primereact/dialog";
import { Button } from "primereact/button";

import { SharePanel, type SharePanelProps } from "./SharePanel";

interface Props extends Omit<SharePanelProps, "active"> {
  visible: boolean;
  onHide: () => void;
  resourceLabel: string;
}

/** Sharing as a dialog of its own — for records and forms. A list's permissions
 *  live in List settings instead, but both render the same SharePanel. */
export function ShareDialog({
  visible,
  onHide,
  resourceLabel,
  ...panel
}: Props) {
  return (
    <Dialog
      header={`Share “${resourceLabel}”`}
      visible={visible}
      onHide={onHide}
      style={{ width: 560 }}
    >
      <div className="flex flex-col gap-4">
        <SharePanel {...panel} active={visible} />
        <div className="flex justify-end">
          <Button label="Done" text onClick={onHide} />
        </div>
      </div>
    </Dialog>
  );
}
