# -*- coding: utf-8 -*-
"""User input collection for Slab Rebar Views — WPF dark UI (matches FlatSlabRFT theme)."""
from __future__ import print_function

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewPlan,
    ElementId,
    BuiltInCategory,
    FamilySymbol,
)
from System.Windows.Markup import XamlReader
from System.Windows import Thickness
from System.Windows.Controls import ListBoxItem, ComboBoxItem, CheckBox
from System.Windows.Input import MouseButton


_XAML = u"""<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="SlabRebarViews"
    Width="660" Height="800"
    WindowStartupLocation="CenterScreen"
    ResizeMode="NoResize"
    WindowStyle="None"
    Background="#09090F">

  <Window.Resources>

    <Style x:Key="SectionHeader" TargetType="TextBlock">
      <Setter Property="Foreground"        Value="#06B6D4"/>
      <Setter Property="FontSize"          Value="10"/>
      <Setter Property="FontWeight"        Value="Bold"/>
      <Setter Property="FontFamily"        Value="Segoe UI"/>
      <Setter Property="VerticalAlignment" Value="Center"/>
    </Style>

    <Style x:Key="FieldLabel" TargetType="TextBlock">
      <Setter Property="Foreground"  Value="#64748B"/>
      <Setter Property="FontSize"    Value="10"/>
      <Setter Property="FontWeight"  Value="SemiBold"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="Margin"      Value="0,0,0,5"/>
    </Style>

    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background"      Value="#111827"/>
      <Setter Property="BorderBrush"     Value="#1E3A5F"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="10"/>
      <Setter Property="Padding"         Value="18,16"/>
      <Setter Property="Margin"          Value="0,0,0,10"/>
    </Style>

    <Style x:Key="RunBtn" TargetType="Button">
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="FontSize"        Value="14"/>
      <Setter Property="FontWeight"      Value="Bold"/>
      <Setter Property="Padding"         Value="36,13"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" CornerRadius="9" Padding="{TemplateBinding Padding}">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.88"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.70"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CancelBtn" TargetType="Button">
      <Setter Property="Background"      Value="#1E293B"/>
      <Setter Property="Foreground"      Value="#94A3B8"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Padding"         Value="24,13"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root"
                    Background="{TemplateBinding Background}"
                    BorderBrush="#334155" BorderThickness="1"
                    CornerRadius="9" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="BorderBrush" Value="#475569"/>
                <Setter Property="Foreground" Value="#CBD5E1"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CloseBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#475569"/>
      <Setter Property="FontSize"        Value="14"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Width"           Value="30"/>
      <Setter Property="Height"          Value="30"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" Background="{TemplateBinding Background}" CornerRadius="5">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Background" Value="#7F1D1D"/>
                <Setter Property="Foreground" Value="#FCA5A5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Ghost pill button (Select All / None) ── -->
    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Foreground"      Value="#06B6D4"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="FontSize"        Value="11"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Padding"         Value="10,4"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root"
                    Background="Transparent"
                    BorderBrush="#06B6D4" BorderThickness="1"
                    CornerRadius="6" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Background" Value="#0E3A4A"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.7"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Dark checkbox with cyan tick ── -->
    <Style x:Key="SlabCheck" TargetType="CheckBox">
      <Setter Property="Foreground"               Value="#E2E8F0"/>
      <Setter Property="FontFamily"               Value="Segoe UI"/>
      <Setter Property="FontSize"                 Value="13"/>
      <Setter Property="Cursor"                   Value="Hand"/>
      <Setter Property="VerticalContentAlignment" Value="Center"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="CheckBox">
            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
              <Border x:Name="Bd"
                      Width="16" Height="16" CornerRadius="4"
                      Background="#1E293B" BorderBrush="#334155" BorderThickness="1"
                      Margin="0,0,10,0" VerticalAlignment="Center">
                <TextBlock x:Name="Mark" Text="&#x2713;"
                           Foreground="#06B6D4" FontSize="11" FontWeight="Bold"
                           HorizontalAlignment="Center" VerticalAlignment="Center"
                           Visibility="Collapsed"/>
              </Border>
              <ContentPresenter VerticalAlignment="Center"/>
            </StackPanel>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="Bd"   Property="BorderBrush" Value="#06B6D4"/>
                <Setter TargetName="Bd"   Property="Background"  Value="#0E3A4A"/>
                <Setter TargetName="Mark" Property="Visibility"  Value="Visible"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Bd" Property="BorderBrush" Value="#475569"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Dark combo item ── -->
    <Style x:Key="DarkComboItem" TargetType="ComboBoxItem">
      <Setter Property="Foreground" Value="#111111"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="Padding"    Value="8,4"/>
      <Style.Triggers>
        <Trigger Property="IsHighlighted" Value="True">
          <Setter Property="Background" Value="#D7ECFF"/>
          <Setter Property="Foreground" Value="#111111"/>
        </Trigger>
        <Trigger Property="IsSelected" Value="True">
          <Setter Property="Background" Value="#2B88D8"/>
          <Setter Property="Foreground" Value="White"/>
        </Trigger>
      </Style.Triggers>
    </Style>

    <!-- ── Dark combo box ── -->
    <Style x:Key="DarkCombo" TargetType="ComboBox">
      <Setter Property="Background"         Value="#1E293B"/>
      <Setter Property="Foreground"         Value="#F1F5F9"/>
      <Setter Property="BorderBrush"        Value="#334155"/>
      <Setter Property="BorderThickness"    Value="1"/>
      <Setter Property="Padding"            Value="10,8"/>
      <Setter Property="FontSize"           Value="13"/>
      <Setter Property="FontFamily"         Value="Segoe UI"/>
      <Setter Property="ItemContainerStyle" Value="{StaticResource DarkComboItem}"/>
    </Style>

  </Window.Resources>

  <Border BorderBrush="#1E3A5F" BorderThickness="1">
    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="40"/>
        <RowDefinition Height="76"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- ═══ TITLE BAR ═══ -->
      <Border Grid.Row="0" x:Name="TitleBar" Background="#0D1117">
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>
          <StackPanel Grid.Column="0" Orientation="Horizontal"
                      VerticalAlignment="Center" Margin="14,0">
            <Ellipse Width="9" Height="9" Margin="0,0,7,0">
              <Ellipse.Fill>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Ellipse.Fill>
            </Ellipse>
            <TextBlock Text="SlabRebarViews" Foreground="#475569" FontSize="11"
                       FontFamily="Segoe UI" VerticalAlignment="Center"/>
          </StackPanel>
          <Border Grid.Column="1" x:Name="DragArea" Background="Transparent"/>
          <Button Grid.Column="2" x:Name="btnClose" Content="&#x2715;"
                  Style="{StaticResource CloseBtn}" Margin="0,0,8,0"/>
        </Grid>
      </Border>

      <!-- ═══ APP HEADER ═══ -->
      <Border Grid.Row="1" Padding="24,0">
        <Border.Background>
          <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
            <GradientStop Color="#0D1B2E" Offset="0"/>
            <GradientStop Color="#0A0E1A" Offset="1"/>
          </LinearGradientBrush>
        </Border.Background>
        <Grid VerticalAlignment="Center">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>
          <StackPanel Grid.Column="0">
            <StackPanel Orientation="Horizontal">
              <Border Width="4" Height="34" CornerRadius="2" Margin="0,0,14,0">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
                    <GradientStop Color="#06B6D4" Offset="0"/>
                    <GradientStop Color="#8B5CF6" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
              <TextBlock Text="SLAB REBAR VIEWS" FontSize="22" FontWeight="Bold"
                         Foreground="#F1F5F9" FontFamily="Segoe UI" VerticalAlignment="Center"/>
            </StackPanel>
            <TextBlock Text="Create plan views with rebar filters and tag annotations"
                       FontSize="12" Foreground="#475569" FontFamily="Segoe UI" Margin="18,4,0,0"/>
          </StackPanel>
          <Border Grid.Column="1" CornerRadius="20" Padding="14,6"
                  VerticalAlignment="Center" BorderThickness="1">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#0E3A4A" Offset="0"/>
                <GradientStop Color="#2D1B69" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
            <Border.BorderBrush>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#06B6D4" Offset="0"/>
                <GradientStop Color="#8B5CF6" Offset="1"/>
              </LinearGradientBrush>
            </Border.BorderBrush>
            <TextBlock FontFamily="Segoe UI" FontSize="11" FontWeight="Bold">
              <Run Text="BIM"    Foreground="#06B6D4"/>
              <Run Text=" · "   Foreground="#475569"/>
              <Run Text="VIEWS" Foreground="#8B5CF6"/>
            </TextBlock>
          </Border>
        </Grid>
      </Border>

      <!-- gradient divider -->
      <Border Grid.Row="1" VerticalAlignment="Bottom" Height="1">
        <Border.Background>
          <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
            <GradientStop Color="#06B6D4"    Offset="0"/>
            <GradientStop Color="#8B5CF6"    Offset="0.5"/>
            <GradientStop Color="Transparent" Offset="1"/>
          </LinearGradientBrush>
        </Border.Background>
      </Border>

      <!-- ═══ CONTENT ═══ -->
      <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" Background="#09090F">
        <StackPanel Margin="24,18,24,8">

          <!-- ── VIEWS TO CREATE ── -->
          <Border Style="{StaticResource Card}">
            <StackPanel>

              <!-- header row: label left, Select All / None right -->
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBlock Text="VIEWS TO CREATE" Style="{StaticResource SectionHeader}"/>
                <StackPanel Grid.Column="1" Orientation="Horizontal">
                  <Button x:Name="btnAll"  Content="All"
                          Style="{StaticResource GhostBtn}" Margin="0,0,6,0"/>
                  <Button x:Name="btnNone" Content="None"
                          Style="{StaticResource GhostBtn}"/>
                </StackPanel>
              </Grid>

              <!-- list box -->
              <Border Background="#0F172A" BorderBrush="#334155" BorderThickness="1"
                      CornerRadius="7">
                <ListBox x:Name="lbViews"
                         Height="210"
                         SelectionMode="Single"
                         Background="Transparent"
                         BorderThickness="0"
                         Foreground="#E2E8F0"
                         FontFamily="Segoe UI"
                         FontSize="13">
                  <ListBox.ItemContainerStyle>
                    <Style TargetType="ListBoxItem">
                      <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                      <Setter Property="Template">
                        <Setter.Value>
                          <ControlTemplate TargetType="ListBoxItem">
                            <Border x:Name="ItemBg"
                                    Background="Transparent"
                                    CornerRadius="6" Margin="4,2" Padding="8,5">
                              <ContentPresenter/>
                            </Border>
                            <ControlTemplate.Triggers>
                              <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="ItemBg" Property="Background" Value="#1E293B"/>
                              </Trigger>
                            </ControlTemplate.Triggers>
                          </ControlTemplate>
                        </Setter.Value>
                      </Setter>
                    </Style>
                  </ListBox.ItemContainerStyle>
                </ListBox>
              </Border>

              <!-- selection counter -->
              <TextBlock x:Name="tbSelCount"
                         Foreground="#475569" FontSize="11" FontFamily="Segoe UI"
                         Margin="2,8,0,0"/>
            </StackPanel>
          </Border>

          <!-- ── VIEW SETTINGS ── -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="VIEW SETTINGS" Style="{StaticResource SectionHeader}"
                         Margin="0,0,0,14"/>
              <TextBlock Text="VIEW TEMPLATE" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbTemplate" Margin="0,0,0,16"
                        Style="{StaticResource DarkCombo}"/>
              <TextBlock Text="REBAR TAG FAMILY" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbTag"
                        Style="{StaticResource DarkCombo}"/>
            </StackPanel>
          </Border>

        </StackPanel>
      </ScrollViewer>

      <!-- ═══ FOOTER ═══ -->
      <Border Grid.Row="3" Background="#0D1117" Padding="24,16,24,22">
        <StackPanel>
          <Border Height="1" Margin="0,0,0,16">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="Transparent" Offset="0"/>
                <GradientStop Color="#334155"     Offset="0.5"/>
                <GradientStop Color="Transparent" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
          </Border>

          <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btnCancel" Content="Cancel"
                    Style="{StaticResource CancelBtn}" Margin="0,0,12,0"/>
            <Button x:Name="btnRun" Content="&#x25B6;  Create Views"
                    Style="{StaticResource RunBtn}"/>
          </StackPanel>

          <!-- Signature -->
          <Border Margin="0,18,0,0" HorizontalAlignment="Center">
            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
              <Border Width="6" Height="6" CornerRadius="3" Margin="0,0,10,0"
                      VerticalAlignment="Center">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                    <GradientStop Color="#06B6D4" Offset="0"/>
                    <GradientStop Color="#8B5CF6" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
              <TextBlock FontFamily="Segoe UI" FontSize="12" VerticalAlignment="Center">
                <Run Text="Developed by "  Foreground="#475569"/>
                <Run Text="Nour Waleed"    Foreground="#06B6D4" FontWeight="Bold"/>
                <Run Text="  &#xB7;  FlatSlabRFT Engine" Foreground="#334155"/>
              </TextBlock>
            </StackPanel>
          </Border>
        </StackPanel>
      </Border>

    </Grid>
  </Border>
</Window>"""


def _get_view_templates(doc):
    collector = FilteredElementCollector(doc).OfClass(ViewPlan)
    templates = []
    for v in collector:
        if v.IsTemplate:
            templates.append((v.Name, v.Id))
    templates.sort(key=lambda t: t[0])
    return templates


def _get_rebar_tag_families(doc):
    collector = (
        FilteredElementCollector(doc)
        .OfClass(FamilySymbol)
        .OfCategory(BuiltInCategory.OST_RebarTags)
    )
    tags = []
    for sym in collector:
        try:
            label = '{} : {}'.format(sym.Family.Name, sym.Name)
            tags.append((label, sym))
        except Exception:
            pass
    tags.sort(key=lambda t: t[0])
    return tags


def collect_inputs(doc, all_view_suffixes):
    """Show the dark WPF dialog and return a params dict, or None if cancelled."""
    window = XamlReader.Parse(_XAML)

    lb_views     = window.FindName('lbViews')
    cb_template  = window.FindName('cbTemplate')
    cb_tag       = window.FindName('cbTag')
    btn_all      = window.FindName('btnAll')
    btn_none     = window.FindName('btnNone')
    tb_sel_count = window.FindName('tbSelCount')
    btn_run      = window.FindName('btnRun')
    btn_cancel   = window.FindName('btnCancel')
    btn_close    = window.FindName('btnClose')
    title_bar    = window.FindName('TitleBar')

    try:
        check_style = window.Resources['SlabCheck']
    except Exception:
        check_style = None

    # ── populate view checkboxes ──
    view_checks = []
    for suffix in all_view_suffixes:
        cb = CheckBox()
        cb.Content   = suffix
        cb.IsChecked = True
        if check_style is not None:
            cb.Style = check_style
        else:
            cb.Foreground = lb_views.Foreground
            cb.FontFamily = lb_views.FontFamily
            cb.FontSize   = lb_views.FontSize
        cb.Margin = Thickness(0, 0, 0, 0)
        item = ListBoxItem()
        item.Content = cb
        item.Tag     = suffix
        lb_views.Items.Add(item)
        view_checks.append(cb)

    # ── selection counter ──
    def _update_count():
        n = sum(1 for ch in view_checks if bool(ch.IsChecked))
        tb_sel_count.Text = '{} of {} views selected'.format(n, len(view_checks))

    for ch in view_checks:
        ch.Checked   += lambda s, e: _update_count()
        ch.Unchecked += lambda s, e: _update_count()

    _update_count()

    # ── populate view template combo ──
    none_item = ComboBoxItem()
    none_item.Content = '<None>'
    none_item.Tag = ElementId.InvalidElementId
    cb_template.Items.Add(none_item)
    for name, tid in _get_view_templates(doc):
        item = ComboBoxItem()
        item.Content = name
        item.Tag = tid
        cb_template.Items.Add(item)
    cb_template.SelectedIndex = 0

    # ── populate rebar tag combo ──
    skip_item = ComboBoxItem()
    skip_item.Content = '<Skip tags>'
    skip_item.Tag = None
    cb_tag.Items.Add(skip_item)
    for label, sym in _get_rebar_tag_families(doc):
        item = ComboBoxItem()
        item.Content = label
        item.Tag = sym
        cb_tag.Items.Add(item)
    cb_tag.SelectedIndex = 0

    result = [None]

    def _on_all(sender, e):
        for ch in view_checks:
            ch.IsChecked = True
        _update_count()

    def _on_none(sender, e):
        for ch in view_checks:
            ch.IsChecked = False
        _update_count()

    def _on_run(sender, e):
        selected = []
        for item, ch in zip(lb_views.Items, view_checks):
            try:
                if bool(ch.IsChecked):
                    selected.append(item.Tag)
            except Exception:
                pass
        if not selected:
            return
        t_item   = cb_template.SelectedItem
        tag_item = cb_tag.SelectedItem
        result[0] = {
            'selected_suffixes': list(selected),
            'view_template_id':  t_item.Tag if t_item is not None else ElementId.InvalidElementId,
            'tag_family_symbol': tag_item.Tag if tag_item is not None else None,
        }
        window.Close()

    def _on_cancel(sender, e):
        window.Close()

    def _on_title_bar_down(sender, e):
        if e.ChangedButton == MouseButton.Left:
            window.DragMove()

    btn_all.Click       += _on_all
    btn_none.Click      += _on_none
    btn_run.Click       += _on_run
    btn_cancel.Click    += _on_cancel
    btn_close.Click     += _on_cancel
    title_bar.MouseDown += _on_title_bar_down

    window.ShowDialog()
    return result[0]
