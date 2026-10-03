/**
 * Medi-Audit Healthcare Discrepancy & Bill Reconciliation SDK
 */

export interface MedicalLineItem {
  itemCode?: string;
  description: string;
  billedAmount: number;
  approvedTariff?: number;
  quantity?: number;
}

export interface AuditInspectionRequest {
  patientId?: string;
  invoiceId?: string;
  hospitalName?: string;
  lineItems: MedicalLineItem[];
  rawTextPayload?: string;
}

export interface DiscrepancyAlert {
  itemDescription: string;
  billedAmount: number;
  statutoryLimit: number;
  variancePercentage: number;
  auditSeverity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  regulatoryClause?: string;
}

export interface AuditReportResponse {
  status: 'COMPLIANT' | 'OVERCHARGED' | 'FLAGGED';
  totalBilled: number;
  totalPermissible: number;
  totalDiscrepancy: number;
  discrepancies: DiscrepancyAlert[];
  aiAuditSummary: string;
  processedAt: string;
}

export interface KafkaHealthcareAuditEvent {
  timestamp: string;
  eventType: 'MEDICAL_BILL_AUDITED';
  invoiceId: string;
  discrepancyCount: number;
  totalOvercharged: number;
  riskSeverity: 'LOW' | 'MEDIUM' | 'HIGH';
}

export class MediAuditClient {
  private endpoint: string;

  constructor(endpoint: string = 'http://localhost:5000') {
    this.endpoint = endpoint.replace(/\/+$/, '');
  }

  /**
   * Submit an invoice for price reconciliation against statutory rates.
   */
  async reconcileInvoice(payload: AuditInspectionRequest): Promise<AuditReportResponse> {
    const res = await fetch(`${this.endpoint}/audit-bill`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(`Medi-Audit reconciliation failed with status: ${res.status}`);
    }

    return (await res.json()) as AuditReportResponse;
  }
}
