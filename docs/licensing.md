# Licensing and redistribution

この文書はTora_MeshForgeのライセンスと再配布条件をまとめたものです。個別案件に対する法的助言ではありません。

## プロジェクトのライセンス

Tora_MeshForge自身のソースコードと文書は、リポジトリ内で別途明記されたものを除きMIT Licenseで公開します。正式な条件はルートの`LICENSE`です。PythonパッケージメタデータにもSPDX式`MIT`を設定し、wheelとsource distributionへ`LICENSE`と`THIRD_PARTY_NOTICES.md`を含めます。

現在の配布方針:

- リポジトリ内に第三者のソースコードやモデル、テクスチャを取り込んでいません。
- Blenderはユーザーが別途入手する外部アプリケーションであり、本配布物へ同梱しません。
- PySide6/Qtはpipが別パッケージとして導入する動的ライブラリで、Tora_MeshForgeのMITコードとは区別します。
- 開発用の作業フォルダ、評価用モデル、テクスチャは公開パッケージから明示的に除外します。

MITは簡潔で、利用・変更・再配布・商用利用を広く許可し、著作権表示とライセンス表示の維持を求めます。プロジェクトの希望に合っており、現時点でコード変更は不要です。

既に第三者へ公開し受領された版のライセンス権は、後から一方的に取り消せません。公開後にライセンスを変更する場合は、各著作権者の同意管理が必要になります。

## 第三者コンポーネント

### Blender

BlenderはGPLで提供されます。Tora_MeshForgeは`blender.exe`を安全な引数配列で起動するだけで、Blender自体を配布しません。Blenderの公式ライセンス情報は <https://developer.blender.org/docs/license/> を参照してください。

### PySide6 / Qt for Python

PySide6、PySide6 Essentials、PySide6 Addons、Shiboken6は、コミュニティ版ではLGPLv3/GPLv3、または商用ライセンスで提供されます。公式情報は <https://doc.qt.io/qtforpython-6/> と <https://doc.qt.io/qtforpython-6/licenses.html> を参照してください。

現在のGitHubソース配布とPython wheelでは、PySide6をTora_MeshForgeの配布物へ内包せず、pipが別途取得します。利用者の仮想環境内にあるPySide6パッケージには上流のライセンス情報が含まれます。

## 配布形態ごとの扱い

### GitHubソースとsource distribution

- `LICENSE`と`THIRD_PARTY_NOTICES.md`を必ず含めます。
- Blender、PySide6、モデル、テクスチャをリポジトリへ同梱しません。

### Python wheel

- wheel内のTora_MeshForgeコードはMITです。
- wheelのメタデータへMITのSPDX式と両ライセンス文書を含めます。
- PySide6はwheelの依存宣言であり、Tora_MeshForge wheelそのものには含めません。

### 単体exe・portable版

PySide6/Qt DLLを同梱する配布物は、現在のソース配布とは別のライセンスレビューが必要です。少なくとも、実際に同梱した全コンポーネントと版の一覧、上流ライセンス全文と著作権表示、LGPLが要求する利用者の権利を妨げない配布方法、該当ライブラリの入手・置換に必要な情報を準備しなければなりません。圧縮・難読化・署名・インストーラー方式も含め、公開前に実際の成果物単位で再確認します。

現在の正式な配布経路は、ソース一式をローカルの`.venv`へインストールする方式です。portable exeは未提供です。

## コントリビューション

Pull Requestとして提出された貢献は、投稿者が提供権限を持ち、プロジェクトのMIT Licenseで配布されることへ同意したものとして受け付けます。外部コード、生成物、モデル、テクスチャを含む場合は、その出所と再配布条件を明記する必要があります。
