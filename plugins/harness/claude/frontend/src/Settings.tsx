import { getHost, type HarnessSettingsProps } from "@qwenpaw/plugin-sdk";
import { CircleHelp, Plus, Trash2 } from "lucide-react";
import { Settings as HarnessSettings } from "../../../_shared/ui/Settings";
import envCss from "./Settings.css?inline";
import { localMessages } from "./locale";

const host = getHost().host;
const React = host.React;
const { Button, Input, Tooltip } = host.antd;

interface EnvRow {
  name: string;
  value: string;
}

/** Show either stored shape as rows; a hand-edited config may use an object. */
function envRows(env: unknown): EnvRow[] {
  if (Array.isArray(env)) {
    return env
      .filter(
        (row): row is Record<string, unknown> =>
          typeof row === "object" && row !== null,
      )
      .map((row) => ({
        name: String(row.name ?? ""),
        value: String(row.value ?? ""),
      }));
  }
  if (env && typeof env === "object") {
    return Object.entries(env as Record<string, unknown>).map(
      ([name, value]) => ({ name, value: String(value ?? "") }),
    );
  }
  return [];
}

export function Settings(props: HarnessSettingsProps) {
  const { value, onChange } = props;
  const text = localMessages(host.useLocale());
  const labelId = React.useId();
  const rows = envRows(value.env);
  const update = (next: EnvRow[]) => onChange({ ...value, env: next });
  const edit = (index: number, change: Partial<EnvRow>) =>
    update(rows.map((row, at) => (at === index ? { ...row, ...change } : row)));
  return (
    <HarnessSettings
      {...props}
      backendId="claude"
      providerName="Claude Code"
      text={text}
      loginMode="external"
      canConnect={false}
    >
      <div className="envField">
        <style>{envCss}</style>
        <span className="binaryLabel" id={labelId}>
          {text.env}
        </span>
        <Tooltip title={text.envHelp}>
          <button type="button" className="helpIcon" aria-label={text.envHelp}>
            <CircleHelp size={14} aria-hidden="true" focusable="false" />
          </button>
        </Tooltip>
        <div className="envList" role="group" aria-labelledby={labelId}>
          {rows.map((row, index) => (
            <div className="envRow" key={index}>
              <Input
                value={row.name}
                placeholder={text.envKeyPlaceholder}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                  edit(index, { name: event.target.value })
                }
              />
              <Input
                value={row.value}
                placeholder={text.envValuePlaceholder}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                  edit(index, { value: event.target.value })
                }
              />
              <Button
                type="text"
                size="small"
                aria-label={text.envRemove}
                icon={<Trash2 size={14} aria-hidden="true" focusable="false" />}
                onClick={() => update(rows.filter((_, at) => at !== index))}
              />
            </div>
          ))}
          <Button
            className="envAdd"
            type="dashed"
            size="small"
            icon={<Plus size={14} aria-hidden="true" focusable="false" />}
            onClick={() => update([...rows, { name: "", value: "" }])}
          >
            {text.envAdd}
          </Button>
        </div>
        <p className="authHint">{text.envHelp}</p>
      </div>
    </HarnessSettings>
  );
}
