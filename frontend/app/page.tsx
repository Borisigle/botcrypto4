export default function Home() {
  return (
    <main className="min-h-screen bg-gray-900 text-white p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-4xl font-bold mb-8">Botcrypto4</h1>
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-semibold mb-4">Order Flow + Liquidation Sweeps Strategy</h2>
          <p className="text-gray-300 mb-4">
            Crypto trading bot focused on detecting liquidation sweeps and confirming with CVD divergence.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-gray-700 rounded p-4">
              <h3 className="text-lg font-medium mb-2">🎯 Strategy</h3>
              <ul className="text-sm text-gray-300 space-y-1">
                <li>• Liquidation sweep detection</li>
                <li>• CVD divergence confirmation</li>
                <li>• Volume Delta spike analysis</li>
                <li>• Risk/Reward: 1:5 to 1:10</li>
              </ul>
            </div>
            <div className="bg-gray-700 rounded p-4">
              <h3 className="text-lg font-medium mb-2">🏗️ Status</h3>
              <ul className="text-sm text-gray-300 space-y-1">
                <li>• Backend: FastAPI ready</li>
                <li>• Frontend: Next.js ready</li>
                <li>• WebSocket: Connected</li>
                <li>• Phase 1: Foundation</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}