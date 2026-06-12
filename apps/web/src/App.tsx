import { useState } from "react";
import { PasteUrlPage } from "./pages/PasteUrlPage";
import { ClosetPage } from "./pages/ClosetPage";

type Tab = "recommend" | "closet";

// Demo shell: a two-tab switch. "Recommend" is the headline moment;
// "Closet" shows the data that powers it. No router lib — keep it tiny.
export function App() {
  const [tab, setTab] = useState<Tab>("recommend");
  return (
    <div className="app">
      <header className="app__header">
        <h1>Sizeify</h1>
        <nav className="app__tabs">
          <button
            className={tab === "recommend" ? "is-active" : ""}
            onClick={() => setTab("recommend")}
          >
            Find my size
          </button>
          <button
            className={tab === "closet" ? "is-active" : ""}
            onClick={() => setTab("closet")}
          >
            My closet
          </button>
        </nav>
      </header>
      <main className="app__main">
        {tab === "recommend" ? <PasteUrlPage /> : <ClosetPage />}
      </main>
    </div>
  );
}
