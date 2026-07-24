# Installation guide

Tora_MeshForgeの現在の正式な配布形態は、GitHubのソース一式をWindows上の仮想環境へインストールする方式です。BlenderとPythonは同梱しません。Qt DLLを内包した単体exe配布は、第三者ライセンス対応を含む別のリリース工程として扱います。

## 必要環境

- Windows 10または11
- Python 3.11以上
- Blender 4.2 LTS以上
- 初回インストール時のインターネット接続（PyPIからPySide6等を取得します）
- 処理対象と中間データを保存できる空き容量

Python 3.11以上が単独でインストールされていない場合、インストーラーは通常のBlenderインストールに含まれるPythonも探索します。

## 推奨インストール

1. GitHubのReleaseまたは`Code > Download ZIP`からソース一式を取得します。
2. ZIPを、継続して使用する任意のフォルダへ展開します。インストール後に元フォルダを移動・削除しないでください。
3. `Install-Tora_MeshForge.bat`をダブルクリックします。
4. 最後にPython、PySide6、Blender、作業フォルダ、同梱スクリプトの診断がすべて`PASS`になったことを確認します。
5. `Tora_MeshForge.bat`をダブルクリックして起動します。

インストーラーはプロジェクト直下の`.venv`だけをPython環境として使用します。システム全体のPython環境へパッケージを追加しません。診断結果は`installation-doctor.json`に保存され、このファイルはGitの公開対象から除外されます。

アップデート時は新しいソースに置き換え、`Install-Tora_MeshForge.bat`を再実行してください。既存の`.venv`を再利用し、アプリを更新します。

## コマンドからのインストール

前提となるPythonだけを確認し、インストールしない場合:

```powershell
.\scripts\install.ps1 -CheckOnly
```

通常インストール:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

開発ツールもインストール:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Dev
```

使用するPythonまたは仮想環境の場所を指定できます:

```powershell
.\scripts\install.ps1 `
  -Python "C:\Path\To\python.exe" `
  -VenvPath ".venv"
```

## 手動インストール

自動インストーラーを使わない場合も仮想環境を使用してください。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install `
  --constraint .\requirements\constraints.txt `
  .
.\.venv\Scripts\tora-meshforge.exe doctor `
  --report .\installation-doctor.json
```

開発用は最後のインストール対象を`".[dev]"`に変更します。

## 起動と診断

GUI:

```powershell
.\Tora_MeshForge.bat
```

CLIのヘルプ:

```powershell
.\.venv\Scripts\tora-meshforge.exe --help
```

環境を再診断:

```powershell
.\Tora_MeshForge.bat --check
```

`doctor`はモデルを読み込まず、ソース資産も変更しません。Blenderはバージョン確認のためバックグラウンドモードで一度だけ起動されます。

## よくある問題

### Python 3.11以上が見つからない

Pythonをインストールしてから再実行するか、`-Python`で`python.exe`を明示します。Microsoft Storeのエイリアスだけが存在し、実体がない場合も明示指定が有効です。

### PySide6の取得に失敗する

初回インストールにはPyPIへ接続できる必要があります。プロキシ、ファイアウォール、証明書エラーを確認してください。現在、完全オフライン用の依存パッケージ一式は配布していません。

### Blenderが見つからない

Blender 4.2 LTS以上を通常の場所へインストールするか、環境変数`BLENDER_PATH`へ`blender.exe`の絶対パスを設定します。GUIの詳細設定またはCLIの`--blender-path`でも指定できます。

### 仮想環境が壊れた

アプリを閉じ、`.venv`を削除してからインストーラーを再実行すると再作成できます。モデル、出力FBX、`work`以外の任意出力先は削除されません。

## アンインストール

Tora_MeshForgeを終了し、展開したプロジェクトフォルダを削除します。インストーラーはWindowsのシステム領域へアプリ本体を登録しません。GUI設定はユーザー設定として残る場合があります。
