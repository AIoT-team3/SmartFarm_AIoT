import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { Leaf, X, AlertCircle } from "lucide-react";

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Leaf size={23} strokeWidth={1.5} />
      </span>
      <p>{title}</p>
      {detail && <span>{detail}</span>}
    </div>
  );
}
export function ErrorNote({ message }: { message: string | null | undefined }) {
  return message ? (
    <div className="error-note" role="alert">
      <AlertCircle size={16} />
      <span>{message}</span>
    </div>
  ) : null;
}
export function Metric({
  label,
  value,
  unit,
  note,
}: {
  label: string;
  value: string;
  unit?: string;
  note?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
      {note && <p>{note}</p>}
    </div>
  );
}
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-inner">
        <div className="modal-heading">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-button" aria-label="닫기" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}
