export default function ErrorState({ title = "Something went wrong", detail, onRetry, testId }) {
  return (
    <div data-testid={testId} role="alert" className="rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center">
      <div className="font-heading text-base font-medium text-red-800">{title}</div>
      {detail && <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-red-600">{detail}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          data-testid={testId ? `${testId}-retry-btn` : undefined}
          className="mt-4 rounded-lg border border-red-300 bg-white px-4 py-2 text-xs font-medium text-red-700 transition-colors duration-200 hover:bg-red-100"
        >
          Try again
        </button>
      )}
    </div>
  );
}
