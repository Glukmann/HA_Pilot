import { Icon } from "../components/Icon";
import type { SectionMeta } from "./sections";
import { STUB_COPY } from "./sections";

export function SectionStub({ meta }: { meta: SectionMeta }) {
  const copy = STUB_COPY[meta.path];
  return (
    <div className="stub">
      <div className="stub-icon">
        <Icon name={meta.icon} size={40} />
      </div>
      <h2>{copy.headline}</h2>
      <p>{copy.body}</p>
    </div>
  );
}
