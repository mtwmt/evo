import Phaser from "phaser";

export type CanvasInteractionMode = "select" | "pan";

/**
 * 通用幾何原語場景：
 * 遵循 README 5.6 核心原則：
 * 繪製抽象幾何原語（質點、連線、文字、粒子），
 * 並支援點擊生命體實體，即時聚焦並展示第一人稱內在獨白。
 */
export class CosmicScene extends Phaser.Scene {
  private graphics!: Phaser.GameObjects.Graphics;
  private labelGroup!: Phaser.GameObjects.Group;
  private bubbleGroup!: Phaser.GameObjects.Group;
  private currentSceneData: any = null;
  public selectedEntityId: string | null = null;
  private readonly minZoom = 0.65;
  private readonly maxZoom = 2.5;
  private interactionMode: CanvasInteractionMode = "select";
  private spaceKey: Phaser.Input.Keyboard.Key | null = null;
  private spacePanActive = false;
  private isPanning = false;
  private panStart: Phaser.Math.Vector2 | null = null;
  private panStartScroll: Phaser.Math.Vector2 | null = null;

  constructor() {
    super({ key: "CosmicScene" });
  }

  create(): void {
    this.graphics = this.add.graphics();
    this.labelGroup = this.add.group();
    this.bubbleGroup = this.add.group();
    this.spaceKey = this.input.keyboard?.addKey(Phaser.Input.Keyboard.KeyCodes.SPACE) ?? null;

    // 監聽畫布點擊事件，偵測是否點擊特定生命體
    this.input.on("pointerdown", (pointer: Phaser.Input.Pointer) => {
      if (!this.currentSceneData?.entities) return;

      // 平常左鍵選取角色；按住空白鍵可暫時拖曳，不必切換工具。
      if (
        pointer.leftButtonDown()
        && (this.interactionMode === "pan" || this.spacePanActive || this.spaceKey?.isDown)
      ) {
        this.isPanning = true;
        this.panStart = new Phaser.Math.Vector2(pointer.x, pointer.y);
        this.panStartScroll = new Phaser.Math.Vector2(this.cameras.main.scrollX, this.cameras.main.scrollY);
        return;
      }

      let found = false;
      const camera = this.cameras.main;
      const worldPoint = camera.getWorldPoint(pointer.x, pointer.y);
      for (const entity of this.currentSceneData.entities) {
        const dx = worldPoint.x - entity.x;
        const dy = worldPoint.y - entity.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        // 實體大小採世界單位，額外熱區固定為 10 個畫面像素。
        const radius = (entity.size || 6) + 10 / camera.zoom;

        if (dist <= radius) {
          this.selectedEntityId = entity.id;
          this.events.emit("entity-selected", entity);
          found = true;
          break;
        }
      }

      if (!found) {
        // 點擊空白處取消鎖定
        this.selectedEntityId = null;
        this.events.emit("entity-deselected");
      }

      this.renderPrimitives();
    });

    this.input.on("pointermove", (pointer: Phaser.Input.Pointer) => {
      if (this.isPanning && pointer.isDown && this.panStart && this.panStartScroll) {
        const camera = this.cameras.main;
        camera.scrollX = this.panStartScroll.x - (pointer.x - this.panStart.x) / camera.zoom;
        camera.scrollY = this.panStartScroll.y - (pointer.y - this.panStart.y) / camera.zoom;
        return;
      }

    });

    this.input.on("pointerup", () => {
      this.isPanning = false;
      this.panStart = null;
      this.panStartScroll = null;
    });

    this.input.on("pointerupoutside", () => {
      this.isPanning = false;
      this.panStart = null;
      this.panStartScroll = null;
    });

    this.input.on("wheel", (_pointer: Phaser.Input.Pointer, _objects: unknown, _dx: number, dy: number) => {
      this.zoomBy(dy > 0 ? -0.12 : 0.12);
    });

    // 明確通知頁面層場景已可接收個體選取事件。
    this.game.events.emit("cosmic-scene-ready", this);
  }

  /**
   * 接收後端 WebSocket 推送之通用場景原語封包並進行繪製。
   */
  public updateScene(sceneData: any): void {
    if (!sceneData) return;
    this.currentSceneData = sceneData;
    this.renderPrimitives();
  }

  public zoomBy(delta: number): void {
    const camera = this.cameras.main;
    camera.setZoom(Phaser.Math.Clamp(camera.zoom + delta, this.minZoom, this.maxZoom));
  }

  public setInteractionMode(mode: CanvasInteractionMode): void {
    this.interactionMode = mode;
    this.isPanning = false;
    this.panStart = null;
    this.panStartScroll = null;
    this.events.emit("interaction-mode-changed", mode);
  }

  /** 頁面層捕捉空白鍵，避免畫布尚未取得焦點時 Phaser 漏掉按鍵。 */
  public setSpacePanActive(active: boolean): void {
    this.spacePanActive = active;
  }

  public resetZoom(): void {
    this.cameras.main.setZoom(1);
    this.cameras.main.centerOn(400, 300);
  }

  private renderPrimitives(): void {
    if (!this.currentSceneData) return;

    this.graphics.clear();
    this.labelGroup.clear(true, true);
    this.bubbleGroup.clear(true, true);

    const { grid, entities, links } = this.currentSceneData;

    // 1. 繪製背景與網格邊界
    if (grid) {
      const bgHex = parseInt((grid.background || "#0b0e14").replace("#", "0x"), 16);
      this.cameras.main.setBackgroundColor(bgHex);

      this.graphics.lineStyle(1, 0x1f2937, 0.4);
      const step = 50;
      for (let x = 0; x <= (grid.width || 800); x += step) {
        this.graphics.lineBetween(x, 0, x, grid.height || 600);
      }
      for (let y = 0; y <= (grid.height || 600); y += step) {
        this.graphics.lineBetween(0, y, grid.width || 800, y);
      }
    }

    // 2. 繪製實體間連線（Links）
    if (Array.isArray(links)) {
      const entityMap = new Map<string, any>();
      if (Array.isArray(entities)) {
        entities.forEach((e) => entityMap.set(e.id, e));
      }

      for (const link of links) {
        const fromEntity = entityMap.get(link.from);
        const toEntity = entityMap.get(link.to);
        if (fromEntity && toEntity) {
          const colorHex = parseInt((link.color || "#4a90e2").replace("#", "0x"), 16);
          const width = link.width || 1;
          this.graphics.lineStyle(width, colorHex, 0.6);
          this.graphics.lineBetween(fromEntity.x, fromEntity.y, toEntity.x, toEntity.y);
        }
      }
    }

    // 3. 繪製實體原語（Entities）
    if (Array.isArray(entities)) {
      for (const entity of entities) {
        const colorHex = parseInt((entity.color || "#00ffcc").replace("#", "0x"), 16);
        const alpha = entity.alpha !== undefined ? entity.alpha : 1.0;
        const size = entity.size || 6;
        const isSelected = this.selectedEntityId === entity.id;

        // 若被選中，繪製聚焦光環
        if (isSelected) {
          this.graphics.lineStyle(2, 0x00ffcc, 0.9);
          this.graphics.strokeCircle(entity.x, entity.y, size + 8);

          this.graphics.lineStyle(1, 0xffffff, 0.6);
          this.graphics.strokeCircle(entity.x, entity.y, size + 14);
        }

        this.graphics.fillStyle(colorHex, alpha);
        if (entity.shape === "rect") {
          this.graphics.fillRect(entity.x - size / 2, entity.y - size / 2, size, size);
        } else {
          this.graphics.fillCircle(entity.x, entity.y, size);
        }

        // 標籤繪製
        const labelText = isSelected ? `★ ${entity.label}` : entity.label;
        const text = this.add.text(entity.x, entity.y + size + 2, labelText, {
          fontSize: isSelected ? "11px" : "9px",
          color: isSelected ? "#00ffcc" : "#94a3b8",
          fontFamily: "monospace",
        });
        text.setOrigin(0.5, 0);
        this.labelGroup.add(text);

        // 若選中該生命體，或該生命體正在發散強烈感應，繪製頭頂浮動內在獨白氣泡
        if ((isSelected || (entity.monologue && entity.state === "超維感應")) && entity.monologue) {
          const bubbleSnippet = entity.monologue.length > 20 ? entity.monologue.slice(0, 19) + "…" : entity.monologue;
          const bubbleX = entity.x;
          const bubbleY = entity.y - size - 24;

          // 氣泡背景底框
          const paddingW = 8;
          const textObj = this.add.text(bubbleX, bubbleY, `💭 ${bubbleSnippet}`, {
            fontSize: "11px",
            color: "#00ffcc",
            backgroundColor: "#151922dd",
            padding: { x: paddingW, y: 4 },
          });
          textObj.setOrigin(0.5, 1);
          this.bubbleGroup.add(textObj);
        }
      }
    }
  }
}
