export type ApiErrorKind = 'http' | 'network' | 'invalid-response';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;

  constructor(message: string, options: { kind: ApiErrorKind; status?: number }) {
    super(message);
    this.name = 'ApiError';
    this.kind = options.kind;
    this.status = options.status;
  }
}

