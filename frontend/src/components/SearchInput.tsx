import { InputText } from "primereact/inputtext";

interface Props {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Sizing is the caller's — a page search fills its column, the records
   *  toolbar's sits at a fixed width beside the other controls. */
  className?: string;
  "aria-label"?: string;
}

/**
 * The app's search box.
 *
 * Five pages each built this from `IconField` + `InputIcon` + `InputText`, and
 * the records toolbar built it a sixth way out of the theme's `p-input-icon-left`
 * plus a hand-counted `pl-9` to clear an icon the reset had not made room for.
 * They are all the same control, so it is one component — and one that can then
 * afford the clear button none of them had, which is otherwise select-all-then-
 * delete every time you want the whole list back.
 */
export function SearchInput({
  value,
  onChange,
  placeholder,
  disabled,
  className = "",
  "aria-label": ariaLabel,
}: Props) {
  return (
    <div className={`search-input ${className}`}>
      <i className="pi pi-search" aria-hidden />
      <InputText
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={ariaLabel ?? placeholder}
        // Escape clears rather than closing anything: inside a dialog the browser
        // would otherwise take a keystroke aimed at the search box and shut the
        // dialog with it.
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) {
            e.stopPropagation();
            onChange("");
          }
        }}
      />
      {value && !disabled && (
        <button
          type="button"
          className="search-clear"
          aria-label="Clear search"
          onClick={() => onChange("")}
        >
          <i className="pi pi-times" />
        </button>
      )}
    </div>
  );
}
