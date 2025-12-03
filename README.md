AI-Assisted Production Scheduling Optimization (Yapay Zeka Destekli Üretim Çizelgeleme)



## 🇹🇷 Proje Hakkında (Turkish)

###  Problem Neydi?
Müşteriye özel makine imalatı yapıyoruz. Yüzlerce alt parça, onlarca istasyon (Kaynak, CNC, Montaj) ve sürekli değişen termin tarihleri... Planlamayı Excel ve insan sezgisiyle yapmak artık imkansızdı. "Acil" bir iş geldiğinde tüm planı manuel kaydırmak günler sürüyordu.

###  Çözüm: Matematiksel Modelleme
Üretim hattını bir **"Kısıt Programlama" (Constraint Programming)** problemi olarak modelledim.

**Sistem Nasıl Çalışıyor?**
1.  **Veri Girişi:** İş emirleri, makine kapasiteleri ve operasyon süreleri sisteme girer.
2.  **Solver (Çözücü):** Google OR-Tools motoru, milyonlarca olası senaryoyu tarar.
3.  **Kısıtlar (Constraints):**
    * *Öncüllük:* "Gövde kaynaklanmadan boyaya giremez."
    * *Kapasite:* "CNC-1 makinesinde aynı anda iki iş olamaz."
    * *Tolerans:* "İşler arasında boşluk bırakma."
4.  **Optimizasyon:** Sistem, **gecikme cezalarını (penalty)** en aza indiren en iyi senaryoyu seçer.

###  Kullanılan Teknolojiler & Yaklaşım
* **Dil:** Python
* **Motor:** Google OR-Tools (CP-SAT Solver)
* **Geliştirme Yöntemi:** Bu projenin algoritmik mantığı ve kısıt denklemleri tarafımca kurgulanmış; kodun yazım süreci **Generative AI** araçları ile hızlandırılmıştır.

---------------------------------------

## 🇬🇧 About the Project (English)

###  The Problem
We manufacture custom machines. With hundreds of sub-parts and varying deadlines, manual planning via Excel became unmanageable. Rescheduling for an "urgent" order used to take days.

###  The Solution: Mathematical Modeling
I modeled the entire production line as a **Constraint Programming (CP)** problem. Instead of relying on intuition, we now rely on mathematical precision.

**How It Works:**
1.  **Input:** Work orders, machine capacities, and operation durations.
2.  **The Solver:** Google OR-Tools engine scans millions of possible scenarios.
3.  **The Constraints:**
    * *Precedence:* "Welding must finish before Painting starts."
    * *No Overlap:* "One machine cannot process two tasks simultaneously."
4.  **Optimization:** The system selects the scenario with the lowest **penalty score** (minimizing delays).

### 🛠 Tech Stack & Approach
* **Language:** Python
* **Engine:** Google OR-Tools (CP-SAT Solver)
* **Development Method:** The algorithmic logic and constraint equations were designed by me; the coding process was accelerated using **Generative AI** tools.

--------------------------------
> **Not:** Bu proje, üretim sahasındaki gerçek bir problemi çözmek için geliştirilmiştir. Şirket verilerini ve kaynak kodları gizli tutulmuştur. Burası projenin **çalışma mantığını ve mühendislik yaklaşımını** gösteren bir vitrindir.
