import type { Localized, Scenario } from '../shared/types';
/** Sample descriptions for the "Dùng mẫu" picker; the backend analyses them like any other text. */
export const sampleCases: Record<Scenario, Localized> = {
  skeletal: {
    vi: 'Thai 30 tuần 2 ngày. Xương đùi và xương cánh tay ngắn không cân đối so với đoạn xa của chi, dưới bách phân vị 3 theo tuổi thai. Chu vi đầu trên bách phân vị 97. Không thấy thông liên thất sau khảo sát. Hình thái bàn tay chưa đánh giá đầy đủ do tư thế thai. Chưa có kết quả xét nghiệm di truyền.',
    en: 'Fetus at 30 weeks and 2 days. Femora and humeri are disproportionately short relative to the distal limb segments, below the 3rd centile for gestational age. Head circumference is above the 97th centile. No ventricular septal defect after assessment. Hand morphology is incompletely assessed due to fetal position. Genetic testing results are not available.'
  },
  prenatal: {
    vi: 'Thai 22 tuần. Ghi nhận thiểu sản xương mũi thai. Không thấy thông liên thất sau khảo sát. Não thất khó đánh giá do tư thế thai.',
    en: 'Fetus at 22 weeks. Hypoplastic fetal nasal bone observed. No ventricular septal defect after assessment. Cerebral ventricles cannot be assessed due to fetal position.'
  },
  ambiguous: { vi: 'Thai 22 tuần. Nghi bất thường tim, chưa xác định cấu trúc. Cần khảo sát bổ sung.', en: 'Fetus at 22 weeks. Suspected cardiac abnormality; structure not determined. Further assessment needed.' },
  empty: { vi: 'Khám thai định kỳ ở tuần 24. Thai máy bình thường. Hẹn siêu âm hình thái theo lịch.', en: 'Routine antenatal visit at 24 weeks. Fetal movements are normal. Anatomy ultrasound is scheduled.' },
  insufficient: { vi: 'Thai 22 tuần. Chưa khảo sát tim. Chưa có bằng chứng để kết luận.', en: 'Fetus at 22 weeks. Heart not assessed. Insufficient evidence for a conclusion.' },
  failure: { vi: 'Thai 28 tuần. Cần đánh giá lại chiều dài xương đùi.', en: 'Fetus at 28 weeks. Repeat femur length assessment is needed.' }
};
