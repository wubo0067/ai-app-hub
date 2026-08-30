# 例 4：两电阻串联与并联的功率计算

**已知：** $R_1 = 4\,\Omega$，$R_2 = 8\,\Omega$，电源电压 $U = 6\,\text{V}$。

## 串联（$R_1$ 与 $R_2$ 串联）

使用公式 $P = I^2 R$：

$$I = \frac{U}{R_1 + R_2} = \frac{6\,\text{V}}{4\,\Omega + 8\,\Omega} = \frac{1}{2}\,\text{A} = 0.5\,\text{A}$$

$$P_1 = I^2 R_1 = \left(\tfrac{1}{2}\right)^2 \times 4 = 1\,\text{W}$$

$$P_2 = I^2 R_2 = \left(\tfrac{1}{2}\right)^2 \times 8 = 2\,\text{W}$$

## 并联（$R_1$ 与 $R_2$ 并联）

使用公式 $P = \dfrac{U^2}{R}$：

$$P_1 = \frac{6^2}{4} = 9\,\text{W}$$

$$P_2 = \frac{6^2}{8} = 4.5\,\text{W}$$

---

# 例 5：灯泡额定与实际功率

**基本参数：** 额定电压 $U_e = 220\,\text{V}$，额定功率 $P_e = 60\,\text{W}$，额定电阻 $R_e$。

## (1) 断路（开路）

- 实际电压：$0\,\text{V}$
- 实际功率：$0\,\text{W}$
- 旁注：$P_{\text{管}}$

## (2) 正常工作

- 实际电压：$220\,\text{V}$
- 实际功率：$60\,\text{W}$
- 旁注：总功率 $= 100\,\text{W}$（即 $60\,\text{W} + 40\,\text{W}$）

## (3) 60 W 灯泡与 40 W 灯泡串联

**电阻计算：**

$$R_{60} = \frac{U_e^2}{P_{60}} = \frac{(220\,\text{V})^2}{60\,\text{W}} \quad \text{（旁注：2）}$$

$$R_{40} = \frac{U_e^2}{P_{40}} = \frac{(220\,\text{V})^2}{40\,\text{W}} \quad \text{（旁注：3）}$$

**分压与功率：**

$$U_{60} = \frac{2}{5} \times 220\,\text{V} = \frac{2}{5}\,U_e \quad (= 88\,\text{V})$$

$$U_{40} = \frac{3}{5} \times 220\,\text{V}$$

$$I = \frac{220\,\text{V}}{R_{60} + R_{40}}$$

$$P_{60} = I^2 R_{60} = \left(\frac{2}{5}\right)^2 R_e \quad (= 9.6\,\text{W})$$