"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef } from "react";
import TemplatePicker from "./TemplatePicker";

type RequesterField = {
  key: string;
  label: string;
  type: string;
  options: Record<string, unknown>;
  required: boolean;
  editable: boolean;
  default_value: unknown;
};

type RequesterFormSchema = {
  ticket_type_id: number;
  name: string;
  pages: Record<string, RequesterField[]>;
};

type RequesterTicketType = {
  id: number;
  name: string;
  itil_class: string;
};

type RequesterTicketFormProps = {
  token: string;
  ticketTypes: RequesterTicketType[];
  ticketType: string;
  onTicketTypeChange: (ticketType: string) => void;
  schema: RequesterFormSchema | null;
  values: Record<string, unknown>;
  onValuesChange: (values: Record<string, unknown>) => void;
  files: File[];
  onFilesChange: (files: File[]) => void;
  canManageTemplates: boolean;
  onApplyTemplate: (values: Record<string, unknown>) => void;
  onRestoreTemplate: (values: Record<string, unknown>) => void;
  onOpenSettings: () => void;
};

function sanitizedEditorHTML(value: string): string {
  const parsed = new DOMParser().parseFromString(value, "text/html");
  const allowedTags = new Set(["P", "BR", "STRONG", "B", "EM", "I", "U", "UL", "OL", "LI", "A"]);
  const suppressedTags = new Set(["SCRIPT", "STYLE", "SVG", "IFRAME", "OBJECT", "TEMPLATE"]);

  function copyNode(node: Node, parent: Node): void {
    if (node.nodeType === Node.TEXT_NODE) {
      parent.appendChild(document.createTextNode(node.textContent ?? ""));
      return;
    }
    if (!(node instanceof Element)) return;
    if (suppressedTags.has(node.tagName)) return;
    if (!allowedTags.has(node.tagName)) {
      node.childNodes.forEach((child) => copyNode(child, parent));
      return;
    }
    const safeElement = document.createElement(node.tagName.toLowerCase());
    if (node.tagName === "A") {
      const href = node.getAttribute("href")?.trim() ?? "";
      if (/^(https?:|mailto:)/i.test(href)) {
        safeElement.setAttribute("href", href);
        safeElement.setAttribute("rel", "nofollow noopener");
      }
    }
    parent.appendChild(safeElement);
    node.childNodes.forEach((child) => copyNode(child, safeElement));
  }

  const output = document.createElement("div");
  parsed.body.childNodes.forEach((node) => copyNode(node, output));
  return output.innerHTML;
}

function RichTextEditor({
  value,
  required,
  onChange,
  label,
}: {
  value: string;
  required: boolean;
  onChange: (value: string) => void;
  label: string;
}) {
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const safeHTML = sanitizedEditorHTML(value);
    if (editor.innerHTML !== safeHTML) editor.innerHTML = safeHTML;
  }, [value]);

  function format(command: string) {
    editorRef.current?.focus();
    document.execCommand(command);
    if (editorRef.current) onChange(editorRef.current.innerHTML);
  }

  return (
    <div className="requester-richtext">
      <div className="requester-richtext-toolbar" aria-label={`${label} formatting`}>
        <button type="button" aria-label="Bold" onMouseDown={(event) => event.preventDefault()} onClick={() => format("bold")}><strong>B</strong></button>
        <button type="button" aria-label="Italic" onMouseDown={(event) => event.preventDefault()} onClick={() => format("italic")}><em>I</em></button>
        <button type="button" aria-label="Bulleted list" onMouseDown={(event) => event.preventDefault()} onClick={() => format("insertUnorderedList")}>• List</button>
      </div>
      <div
        ref={editorRef}
        className="requester-richtext-editor"
        contentEditable
        role="textbox"
        aria-label={label}
        aria-multiline="true"
        aria-required={required}
        onInput={(event) => onChange(event.currentTarget.innerHTML)}
      />
    </div>
  );
}

export default function RequesterTicketForm({
  token,
  ticketTypes,
  ticketType,
  onTicketTypeChange,
  schema,
  values,
  onValuesChange,
  files,
  onFilesChange,
  canManageTemplates,
  onApplyTemplate,
  onRestoreTemplate,
  onOpenSettings,
}: RequesterTicketFormProps) {
  const fields = useMemo(
    () => Object.values(schema?.pages ?? {}).flat().filter((field) => field.editable),
    [schema],
  );
  const priorityField = fields.find((field) => field.key === "priority");
  const currentValues: Record<string, unknown> = {
    ...values,
    ...(priorityField && values.priority == null && priorityField.default_value != null
      ? { priority: priorityField.default_value }
      : {}),
  };

  function setFieldValue(key: string, value: unknown) {
    onValuesChange({ ...values, [key]: value });
  }

  function addFiles(selected: FileList | File[]) {
    const additions = Array.from(selected);
    if (additions.length) onFilesChange([...files, ...additions]);
  }

  function renderField(field: RequesterField) {
    const value = currentValues[field.key] ?? "";
    const widget = field.options.widget;
    if (widget === "attachments") {
      return (
        <label className="file-picker" key={`${field.key}-${schema?.ticket_type_id}`}>
          {field.label}
          <span className="drop-zone" onDragOver={(event: DragEvent<HTMLSpanElement>) => event.preventDefault()} onDrop={(event: DragEvent<HTMLSpanElement>) => {
            event.preventDefault();
            addFiles(event.dataTransfer.files);
          }}>
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.txt,application/pdf,image/png,image/jpeg,text/plain"
              multiple
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                addFiles(event.target.files ?? []);
                event.target.value = "";
              }}
            />
            <strong>Drop files here or choose files</strong>
            <small>PDF, PNG, JPG, or TXT. Up to 10 MB per file.</small>
          </span>
          {files.length > 0 && (
            <span className="file-list">
              {files.map((file, index) => (
                <span key={`${file.name}-${file.size}-${index}`}>
                  {file.name}
                  <b>{(file.size / 1024).toFixed(0)} KB</b>
                </span>
              ))}
            </span>
          )}
        </label>
      );
    }

    if (field.type === "richtext") {
      return (
        <label key={`${field.key}-${schema?.ticket_type_id}`}>
          {field.label}
          <RichTextEditor
            value={String(value)}
            required={field.required}
            label={field.label}
            onChange={(next) => setFieldValue(field.key, next)}
          />
        </label>
      );
    }

    if (field.type === "select") {
      const choices = Array.isArray(field.options.choices) ? field.options.choices as unknown[] : [];
      const choiceLabels = field.options.choice_labels as Record<string, string> | undefined;
      return (
        <label key={`${field.key}-${schema?.ticket_type_id}`}>
          {field.label}
          <select
            value={String(value)}
            required={field.required}
            onChange={(event) => setFieldValue(field.key, event.target.value)}
          >
            <option value="">Select {field.label.toLowerCase()}</option>
            {choices.map((choice) => (
              <option key={String(choice)} value={String(choice)}>
                {choiceLabels?.[String(choice)] ?? String(choice)}
              </option>
            ))}
          </select>
        </label>
      );
    }

    if (field.type === "textarea") {
      return (
        <label key={`${field.key}-${schema?.ticket_type_id}`}>
          {field.label}
          <textarea value={String(value)} required={field.required} onChange={(event) => setFieldValue(field.key, event.target.value)} />
        </label>
      );
    }

    return (
      <label key={`${field.key}-${schema?.ticket_type_id}`}>
        {field.label}
        <input
          type={field.type === "number" ? "number" : "text"}
          value={String(value)}
          required={field.required}
          onChange={(event) => setFieldValue(field.key, field.type === "number" ? Number(event.target.value) : event.target.value)}
        />
      </label>
    );
  }

  const selectedType = ticketTypes.find((type) => String(type.id) === ticketType);

  return (
    <>
      <label>
        What do you need?
        <select value={ticketType} onChange={(event) => onTicketTypeChange(event.target.value)} required>
          <option value="">Select a request type</option>
          {ticketTypes.map((type) => (
            <option key={type.id} value={type.id}>
              {type.itil_class === "INCIDENT" ? "Report an issue · Incident" : "Request a service · Service Request"}
            </option>
          ))}
        </select>
        <small className="form-help">
          Choose Incident for an interruption or fault. Choose Service Request for access, hardware, software, or another service.
        </small>
      </label>
      {selectedType && (
        <TemplatePicker
          token={token}
          ticketTypeId={ticketType}
          currentValues={currentValues}
          hasExistingInput={Object.values(currentValues).some((value) => value !== "" && value != null)}
          canManageTemplates={canManageTemplates}
          onApply={(prefilled) => {
            const chosenPriority = priorityField
              ? currentValues[priorityField.key]
              : undefined;
            onApplyTemplate({
              ...prefilled,
              ...(chosenPriority == null ? {} : { [priorityField?.key ?? ""]: chosenPriority }),
            });
          }}
          onRestore={onRestoreTemplate}
          onOpenSettings={onOpenSettings}
        />
      )}
      {schema
        ? <div className="requester-ticket-fields">{fields.map(renderField)}</div>
        : <p className="form-help">Loading the selected ticket form…</p>}
    </>
  );
}
