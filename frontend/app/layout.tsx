import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Polymarket Backtest",
  description: "Backtest trading strategies on Polymarket prediction markets",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <nav className="border-b border-slate-700 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-14">
              <Link href="/" className="flex items-center gap-2 font-bold text-lg text-blue-400 hover:text-blue-300">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                Polymarket Backtest
              </Link>
              <div className="flex items-center gap-6 text-sm">
                <Link href="/" className="text-slate-300 hover:text-white transition-colors">Dashboard</Link>
                <Link href="/strategy" className="text-slate-300 hover:text-white transition-colors">Run Backtest</Link>
                <Link href="/sweep" className="text-slate-300 hover:text-white transition-colors">Sweep</Link>
                <a
                  href="https://polymarket-backtest-ir3p.onrender.com/docs"
                  target="_blank"
                  className="text-slate-300 hover:text-white transition-colors"
                >
                  API Docs
                </a>
              </div>
            </div>
          </div>
        </nav>
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
