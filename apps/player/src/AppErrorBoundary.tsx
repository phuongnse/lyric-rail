import { Component, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  reload?: () => void;
};

type State = { failed: boolean };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="fatal-ui" role="alert" aria-labelledby="fatal-ui-title">
        <div className="fatal-ui-card">
          <p className="eyebrow">Player interface</p>
          <h1 id="fatal-ui-title">The interface needs to reload</h1>
          <p>LyricRail contained an unexpected display error. Native background work, including an active model installation, may still be running.</p>
          <button onClick={() => (this.props.reload ?? (() => window.location.reload()))()}>Reload interface</button>
        </div>
      </main>
    );
  }
}
